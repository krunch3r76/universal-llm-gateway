"""Dedicated veto pass for authority-accepted claims.

Runs a separate verification pool on claims that a domain authority
(e.g. zyphra_math) accepted.  Only unanimous FALSE from all veto
models overrides the authority verdict — a high bar that prevents
casual general-model disagreement from discarding specialist results.

When no authority-accepted claims exist or no veto pool is configured,
passes through the original data unchanged.

Placement: after tiebreaker_pass, before post_process.
Tiebreaker adjusts borderline *general* claims; veto adjusts
*authority-accepted* claims.  Disjoint populations, independent steps.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, override

from systems.pipeline.core.events.verification import VetoPassCompleted
from systems.pipeline.core.execution.chunked import get_execution_config
from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

from ._chain_verification import verify_claims
from ._threshold import get_policy_fn

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)


def _strip_parent_context(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove parent_text/parent_statement_id from claims for veto re-verification.

    During initial verification, parent annotations help models disambiguate
    subclaims extracted from compound statements.  In the veto pass, these
    annotations cause models to evaluate the original compound claim rather
    than the subclaim itself — producing incorrect verdicts.
    """
    excluded = {"parent_text", "parent_statement_id"}
    return [{k: v for k, v in claim.items() if k not in excluded} for claim in claims]


class VetoPassHandler(BaseHandler):
    """Re-verify authority-accepted claims with an independent pool.

    Reads authority_verdicts from a preceding verify_chain step to
    identify claims where the domain authority said TRUE and
    veto_policy was disabled (final=True).  Those claims are sent
    to the veto_pool for independent verification.

    Veto policy (configurable, default unanimous_reject):
        unanimous_reject → claim only rejected when ALL veto models
        vote FALSE.  Any single TRUE preserves the authority verdict.
    """

    step_type: str = "consensus_veto_pass_v4"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        """Run veto verification on authority-accepted claims, or passthrough."""
        start_time = time.time()

        resolver = NamespaceResolver(context)
        verified_facts = self._resolve_input(
            resolver, step, "verified_facts", step.handler_inputs
        )
        rejected_claims = self._resolve_input(
            resolver, step, "rejected_claims", step.handler_inputs
        )
        authority_verdicts_raw = self._resolve_input(
            resolver, step, "authority_verdicts", step.handler_inputs
        )
        question = str(
            self._resolve_input(resolver, step, "question", step.handler_inputs) or ""
        )

        verified_list: list[dict[str, Any]] = (
            list(verified_facts) if verified_facts else []
        )
        rejected_list: list[dict[str, Any]] = (
            list(rejected_claims) if rejected_claims else []
        )
        authority_verdicts: dict[str, dict[str, Any]] = (
            dict(authority_verdicts_raw) if authority_verdicts_raw else {}
        )

        passthrough = StepOutput(
            raw="",
            json={
                "verified_facts": verified_list,
                "rejected_claims": rejected_list,
            },
            step_id=step.id,
        )

        # Identify authority-accepted claims (final=True, verdict=True)
        authority_accepted_ids = {
            sid
            for sid, v in authority_verdicts.items()
            if v.get("verdict") and v.get("final")
        }

        if not authority_accepted_ids:
            logger.info(
                "Step '%s': no authority-accepted claims, veto passthrough", step.id
            )
            return passthrough

        authority_claims = [
            c
            for c in verified_list
            if c.get("statement_id", "") in authority_accepted_ids
        ]

        if not authority_claims:
            logger.info(
                "Step '%s': authority-accepted IDs found but no matching "
                "verified_facts, veto passthrough",
                step.id,
            )
            return passthrough

        # Resolve veto pool
        veto_pool_aliases = self._resolve_veto_pool(step, context)
        if not veto_pool_aliases:
            logger.info("Step '%s': no veto_pool configured, passthrough", step.id)
            return passthrough

        veto_model_ids = [
            self._resolve_model_alias(alias, context) for alias in veto_pool_aliases
        ]

        # Resolve exec configs per model
        registry = context._registry
        exec_configs = {}
        verification_chunk_size = step.get_domain_field("verification_chunk_size")
        if verification_chunk_size is None:
            verification_chunk_size = context.pipeline.options.get(
                "verification_chunk_size"
            )
        for alias in veto_pool_aliases:
            model_config = registry.get_model_config(
                alias, domain=context.pipeline.domain
            )
            exec_config = get_execution_config(model_config)
            if verification_chunk_size is not None:
                exec_config.chunk_size = min(
                    int(verification_chunk_size), exec_config.chunk_size
                )
            exec_configs[model_config.model] = exec_config

        prompt_ref_verify = str(step.get_domain_field("prompt_ref_verify") or "")
        if not prompt_ref_verify:
            logger.error("Step '%s': missing prompt_ref_verify for veto pass", step.id)
            return passthrough

        prompt_ref_batch = step.get_domain_field("prompt_ref_verify_batch")

        logger.info(
            "Step '%s': veto pass — %d authority-accepted claims, %d veto models %s",
            step.id,
            len(authority_claims),
            len(veto_model_ids),
            veto_pool_aliases,
        )

        # Strip parent_text from claims for veto re-verification.
        # During initial verification, parent_text provides disambiguation
        # context for decomposed subclaims.  In veto, it causes models to
        # evaluate the original compound claim instead of the subclaim.
        veto_candidates = _strip_parent_context(authority_claims)

        # Verify authority-accepted claims with veto pool
        veto_verdicts, verdicts_by_model, model_timings = await verify_claims(
            handler=self,
            candidates=veto_candidates,
            question=question,
            verify_model_ids=veto_model_ids,
            step=step,
            context=context,
            prompt_ref=prompt_ref_verify,
            exec_configs=exec_configs,
            prompt_ref_verify_batch=(
                str(prompt_ref_batch) if prompt_ref_batch else None
            ),
        )

        # Remap model IDs to aliases for readability
        id_to_alias = dict(zip(veto_model_ids, veto_pool_aliases, strict=True))
        verdicts_by_model = {
            id_to_alias.get(mid, mid): v for mid, v in verdicts_by_model.items()
        }

        # Apply veto policy: only override when threshold met
        veto_policy_name = str(
            step.get_domain_field("veto_policy") or "unanimous_reject"
        )
        policy_fn = get_policy_fn(veto_policy_name)
        vetoed, survived = self._apply_veto_threshold(
            authority_claims, veto_verdicts, policy_fn
        )

        vetoed_ids = {c.get("statement_id", "") for c in vetoed}
        survived_ids = {c.get("statement_id", "") for c in survived}

        # Rebuild verified/rejected lists
        updated_verified = [
            c for c in verified_list if c.get("statement_id") not in vetoed_ids
        ]
        updated_rejected = rejected_list + vetoed

        latency_ms = (time.time() - start_time) * 1000
        veto_latency = sum(t.latency_ms for t in model_timings)

        logger.info(
            "Step '%s': veto pass complete — %d checked, %d vetoed, "
            "%d survived (%.0fms verify, %.0fms total)",
            step.id,
            len(authority_claims),
            len(vetoed),
            len(survived),
            veto_latency,
            latency_ms,
        )

        # Emit veto event for pipeline viewer
        recorder = context.recorder
        if recorder:
            recorder.emit(
                VetoPassCompleted(
                    step_name=step.id,
                    authority_claims_checked=len(authority_claims),
                    vetoed_ids=list(vetoed_ids),
                    survived_ids=list(survived_ids),
                    veto_pool=veto_pool_aliases,
                    verdicts_by_model=verdicts_by_model,
                    veto_policy=veto_policy_name,
                    latency_ms=round(veto_latency, 2),
                )
            )

        return StepOutput(
            raw="",
            json={
                "verified_facts": updated_verified,
                "rejected_claims": updated_rejected,
                "veto_stats": {
                    "authority_claims_checked": len(authority_claims),
                    "vetoed": len(vetoed),
                    "survived": len(survived),
                    "pool": veto_pool_aliases,
                    "policy": veto_policy_name,
                    "latency_ms": round(veto_latency, 2),
                },
            },
            step_id=step.id,
            latency_ms=latency_ms,
        )

    def _resolve_veto_pool(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> list[str]:
        """Resolve veto pool model aliases from step config or pipeline options."""
        pool = step.get_domain_field("veto_pool")
        if isinstance(pool, list):
            return pool
        if isinstance(pool, str):
            options = context.options or {}
            resolved = options.get(pool.split(".")[-1], [])
            if isinstance(resolved, list):
                return resolved
        return []

    @staticmethod
    def _apply_veto_threshold(
        claims: list[dict[str, Any]],
        verdicts: dict[str, list[bool]],
        policy_fn: Any,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Partition claims into vetoed and survived based on veto policy.

        A claim is vetoed when its TRUE vote count falls below the
        policy threshold.  With unanimous_reject (threshold=1), this
        means zero TRUE votes — every model voted FALSE.
        """
        vetoed: list[dict[str, Any]] = []
        survived: list[dict[str, Any]] = []
        for claim in claims:
            sid = claim.get("statement_id", "")
            votes = verdicts.get(sid, [])
            true_count = sum(1 for v in votes if v)
            required = policy_fn(len(votes)) if votes else 1
            if true_count >= required:
                survived.append(claim)
            else:
                vetoed.append(claim)
        return vetoed, survived

    @override
    def validate(self, step: StepConfig) -> list[str]:
        """Validate step configuration."""
        errors: list[str] = []
        inputs = step.handler_inputs or {}
        for required in ("verified_facts", "rejected_claims", "authority_verdicts"):
            if required not in inputs:
                errors.append(
                    f"Step '{step.id}' missing '{required}' in handler_inputs"
                )
        return errors

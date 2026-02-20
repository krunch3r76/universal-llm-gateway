"""Run veto verification on authority-accepted claims."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

from provenance.cross_model import order_models_by_affinity
from systems.pipeline.core.execution.chunked import get_execution_config
from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

from ...shared._chain_verification import verify_claims

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)


def _strip_parent_context(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove parent annotations that bias veto re-verification."""
    excluded = {"parent_text", "parent_statement_id"}
    return [{k: v for k, v in c.items() if k not in excluded} for c in claims]


class VetoVerifyHandler(BaseHandler):
    """Identify authority-accepted claims and run veto verification."""

    step_type: str = "consensus_veto_verify_v6_0"

    def _resolve_veto_pool(
        self, step: StepConfig, context: PipelineContext
    ) -> list[str]:
        pool = step.get_domain_field("veto_pool")
        if isinstance(pool, list):
            return pool
        if isinstance(pool, str):
            options = context.options or {}
            resolved = options.get(pool.split(".")[-1], [])
            if isinstance(resolved, list):
                return resolved
        return []

    @override
    async def execute(
        self, step: StepConfig, context: PipelineContext
    ) -> StepOutput:
        resolver = NamespaceResolver(context)
        verified_facts: list[dict[str, Any]] = self._resolve_input(
            resolver, step, "verified_facts", step.handler_inputs
        )
        authority_verdicts_raw = self._resolve_input(
            resolver, step, "authority_verdicts", step.handler_inputs
        )
        question = str(
            self._resolve_input(resolver, step, "question", step.handler_inputs) or ""
        )

        verified_list = list(verified_facts) if verified_facts else []
        authority_verdicts: dict[str, dict[str, Any]] = (
            dict(authority_verdicts_raw) if authority_verdicts_raw else {}
        )

        # Find authority-accepted claims (final=True, verdict=True)
        authority_accepted_ids = {
            sid
            for sid, v in authority_verdicts.items()
            if v.get("verdict") and v.get("final")
        }

        if not authority_accepted_ids:
            return StepOutput(
                raw="",
                json={"veto_verdicts": {}, "verdicts_by_model": {}, "authority_claims": []},
            )

        authority_claims = [
            c for c in verified_list
            if c.get("statement_id", "") in authority_accepted_ids
        ]
        if not authority_claims:
            return StepOutput(
                raw="",
                json={"veto_verdicts": {}, "verdicts_by_model": {}, "authority_claims": []},
            )

        veto_pool_aliases = self._resolve_veto_pool(step, context)
        if not veto_pool_aliases:
            logger.warning("Step '%s': no veto_pool configured", step.id)
            return StepOutput(
                raw="",
                json={"veto_verdicts": {}, "verdicts_by_model": {}, "authority_claims": authority_claims},
            )

        # Affinity ordering
        answer_models_opt = (context.options or {}).get("answer_models", {})
        answer_pool: set[str] = (
            set(answer_models_opt.values())
            if isinstance(answer_models_opt, dict)
            else set(answer_models_opt)
            if isinstance(answer_models_opt, list)
            else set()
        )
        if answer_pool:
            veto_pool_aliases = order_models_by_affinity(veto_pool_aliases, answer_pool)

        veto_model_ids = [
            self._resolve_model_alias(alias, context) for alias in veto_pool_aliases
        ]

        registry = context._registry
        exec_configs: dict[str, Any] = {}
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
        prompt_ref_batch = step.get_domain_field("prompt_ref_verify_batch")

        veto_candidates = _strip_parent_context(authority_claims)

        veto_verdicts, verdicts_by_model, _timings = await verify_claims(
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

        id_to_alias = dict(zip(veto_model_ids, veto_pool_aliases, strict=True))
        verdicts_by_model = {
            id_to_alias.get(mid, mid): v for mid, v in verdicts_by_model.items()
        }

        return StepOutput(
            raw="",
            json={
                "veto_verdicts": veto_verdicts,
                "verdicts_by_model": verdicts_by_model,
                "authority_claims": authority_claims,
            },
        )

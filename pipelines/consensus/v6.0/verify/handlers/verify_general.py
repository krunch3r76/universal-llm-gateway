"""Cross-model general verification with affinity ordering."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

from provenance.cross_model import order_models_by_affinity
from systems.pipeline.core.execution.chunked import (
    ModelExecutionConfig,
    get_execution_config,
)
from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

from ...shared._chain_verification import verify_claims

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)


class VerifyGeneralHandler(BaseHandler):
    """Run cross-model verification on general claims."""

    step_type: str = "consensus_verify_general_v6_0"

    def _resolve_verify_models(
        self,
        step: StepConfig,
        context: PipelineContext,
        originator: str | None,
    ) -> list[str]:
        """Resolve model pool, excluding originator if configured."""
        pool_raw = step.get_domain_field("model_pool")
        if isinstance(pool_raw, list):
            aliases = list(pool_raw)
        elif isinstance(pool_raw, str):
            opts = context.options or {}
            resolved = opts.get(pool_raw.split(".")[-1], [])
            aliases = list(resolved) if isinstance(resolved, list) else []
        else:
            aliases = []

        exclude_self = step.get_domain_field("exclude_self", False)
        if exclude_self and originator:
            aliases = [a for a in aliases if a != originator]
        return aliases

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        resolver = NamespaceResolver(context)
        claims_for_general: list[dict[str, Any]] = self._resolve_input(
            resolver, step, "claims_for_general", step.handler_inputs
        )
        question = str(
            self._resolve_input(resolver, step, "question", step.handler_inputs) or ""
        )
        originator = self._resolve_input(
            resolver, step, "originator", step.handler_inputs
        )
        originator = str(originator) if originator is not None else None

        verify_model_aliases = self._resolve_verify_models(step, context, originator)

        # Affinity ordering: answer-pool models first
        answer_models_opt = (context.options or {}).get("answer_models", {})
        answer_pool: set[str] = (
            set(answer_models_opt.values())
            if isinstance(answer_models_opt, dict)
            else set(answer_models_opt)
            if isinstance(answer_models_opt, list)
            else set()
        )
        if answer_pool:
            verify_model_aliases = order_models_by_affinity(
                verify_model_aliases, answer_pool
            )

        verify_model_ids = [
            self._resolve_model_alias(alias, context) for alias in verify_model_aliases
        ]

        # Build execution configs per model
        registry = context._registry
        exec_configs: dict[str, ModelExecutionConfig] = {}
        verification_chunk_size = step.get_domain_field("verification_chunk_size")
        if verification_chunk_size is None:
            verification_chunk_size = context.pipeline.options.get(
                "verification_chunk_size"
            )
        for alias in verify_model_aliases:
            model_config = registry.get_model_config(
                alias, domain=context.pipeline.domain
            )
            exec_config = get_execution_config(model_config)
            if verification_chunk_size is not None:
                exec_config.chunk_size = min(
                    int(verification_chunk_size), exec_config.chunk_size
                )
            exec_configs[model_config.model] = exec_config

        prompt_ref_verify = self._require_domain_field(step, "prompt_ref_verify")
        prompt_ref_verify_batch = step.get_domain_field("prompt_ref_verify_batch")

        verdicts, verdicts_by_model, model_timings = await verify_claims(
            handler=self,
            candidates=claims_for_general,
            question=question,
            verify_model_ids=verify_model_ids,
            step=step,
            context=context,
            prompt_ref=prompt_ref_verify,
            exec_configs=exec_configs,
            prompt_ref_verify_batch=prompt_ref_verify_batch,
        )

        # Remap verdicts_by_model keys: resolved model ID → alias
        id_to_alias: dict[str, str] = dict(
            zip(verify_model_ids, verify_model_aliases, strict=True)
        )
        verdicts_by_model = {
            id_to_alias.get(mid, mid): v for mid, v in verdicts_by_model.items()
        }

        timing_data = {
            "total_models": len(model_timings),
            "total_latency_ms": round(sum(t.latency_ms for t in model_timings), 2),
            "per_model": [
                {
                    "model_id": t.model_id,
                    "num_claims": t.num_claims,
                    "latency_ms": round(t.latency_ms, 2),
                    "mode": t.mode,
                    "chunk_size": t.chunk_size,
                }
                for t in model_timings
            ],
        }

        # Preliminary majority classification for viewer display.
        # Definitive classification (with math policy, compound parents) is applied
        # by the downstream filter_threshold step.
        verified_facts: list[dict[str, Any]] = []
        rejected_claims: list[dict[str, Any]] = []
        for claim in claims_for_general:
            sid = claim.get("statement_id", "")
            votes = verdicts.get(sid, [])
            accepted = bool(votes) and sum(1 for v in votes if v) > len(votes) / 2
            (verified_facts if accepted else rejected_claims).append(claim)

        return StepOutput(
            raw="",
            json={
                "verdicts": verdicts,
                "verdicts_by_model": verdicts_by_model,
                "verification_timing": timing_data,
                "verified_facts": verified_facts,
                "rejected_claims": rejected_claims,
            },
        )

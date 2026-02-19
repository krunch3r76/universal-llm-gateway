"""Classify claims by domain (math, general, etc.)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

from ...shared._chain_utils import classify_claims

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)


class ClassifyDomainHandler(BaseHandler):
    """Classify each claim's domain (math vs general)."""

    step_type: str = "consensus_classify_domain_v6_0"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        resolver = NamespaceResolver(context)
        claims: list[dict[str, Any]] = self._resolve_input(
            resolver, step, "claims", step.handler_inputs
        )
        if not claims:
            return StepOutput(raw="", json={"claims": []})

        if not step.model_ref:
            raise ValueError(f"Step '{step.id}' missing model_ref")
        model_id = self._resolve_model_alias(step.model_ref, context)

        prompt_ref = str(step.get_domain_field("prompt_ref_classify") or "")
        chunk_size = step.get_domain_field("classify_chunk_size")
        if chunk_size is None:
            chunk_size = context.pipeline.options.get("classify_chunk_size")

        claims = await classify_claims(
            self,
            claims,
            model_id,
            step,
            context,
            prompt_ref,
            chunk_size=int(chunk_size) if chunk_size is not None else None,
        )

        return StepOutput(raw="", json={"claims": claims})

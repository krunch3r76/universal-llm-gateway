"""Decompose compound claims into atomic sub-claims."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

from ...shared._decompose_compound import decompose_compound_general_claims

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)


class DecomposeCompoundHandler(BaseHandler):
    """Split heuristic-detected compound claims into atomic sub-claims."""

    step_type: str = "consensus_decompose_compound_v6_0"

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
            return StepOutput(raw="", json={"claims": [], "compound_details": []})

        if not step.model_ref:
            raise ValueError(f"Step '{step.id}' missing model_ref")
        model_id = self._resolve_model_alias(step.model_ref, context)

        decompose_general = step.get_domain_field("decompose_compound_general", True)
        decompose_math = step.get_domain_field("decompose_compound_math", True)
        domains: set[str] = set()
        if decompose_general:
            domains.add("general")
        if decompose_math:
            domains.add("math")

        prompt_ref = str(
            step.get_domain_field("prompt_ref_decompose_compound")
            or "consensus.v4.0.decompose_general_compound"
        )

        claims, compound_details = await decompose_compound_general_claims(
            handler=self,
            claims=claims,
            model_id=model_id,
            step=step,
            context=context,
            prompt_ref=prompt_ref,
            domains=frozenset(domains),
        )

        return StepOutput(
            raw="",
            json={"claims": claims, "compound_details": compound_details},
        )

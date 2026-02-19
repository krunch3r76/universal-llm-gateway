"""LLM-based atomicity gate for compound claims missed by heuristics."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

from ...shared._decompose_compound import atomicity_gate_decompose

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)


class AtomicityGateHandler(BaseHandler):
    """Catch compound claims that the heuristic decomposition missed."""

    step_type: str = "consensus_atomicity_gate_v6_0"

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

        decompose_math = step.get_domain_field("decompose_compound_math", True)
        gate_domains: set[str] = {"general"}
        if decompose_math:
            gate_domains.add("math")

        prompt_ref_classify = str(
            step.get_domain_field("prompt_ref_atomicity_classify")
            or "consensus.v4.0.classify_atomicity"
        )
        prompt_ref_decompose = str(
            step.get_domain_field("prompt_ref_decompose_compound")
            or "consensus.v4.0.decompose_general_compound"
        )

        claims, _details = await atomicity_gate_decompose(
            handler=self,
            claims=claims,
            model_id=model_id,
            step=step,
            context=context,
            prompt_ref_classify=prompt_ref_classify,
            prompt_ref_decompose=prompt_ref_decompose,
            domains=frozenset(gate_domains),
        )

        return StepOutput(raw="", json={"claims": claims})

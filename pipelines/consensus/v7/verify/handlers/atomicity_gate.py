"""Ensure every claim is atomic before verification.

Some claims produced by decompose are compound — they bundle multiple
independent assertions into a single statement.  Compound claims are
problematic for verification because a single false sub-assertion
would reject the entire compound, losing the true sub-assertions.

This step uses an LLM to:

1. **Classify** each claim as atomic or compound.
2. **Decompose** compound claims into atomic sub-claims, linking each
   sub-claim back to its parent via ``parent_statement_id``.

The parent claim is retained with ``has_sub_claims=True`` so that
filter_threshold can later derive the parent's verdict from the
conjunction of its sub-claim verdicts (parent passes iff ALL
sub-claims pass).

Configurable per domain — ``decompose_compound_math`` controls whether
math-domain compounds are also decomposed.

Outputs:
    json.claims            — updated claim list (compounds replaced by sub-claims)
    json.compound_details  — decomposition metadata for the viewer
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

from systems.pipeline.core.execution.chunked.model_config import get_execution_config
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
    """Split compound claims into independently verifiable atomic sub-claims.

    Compounds are replaced by their sub-claims in the output list;
    the parent is preserved with ``has_sub_claims=True`` for downstream
    conjunction logic in filter_threshold.
    """

    step_type: str = "consensus_atomicity_gate_v7"

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

        alias = step.model_ref
        if alias.startswith("optionsNs."):
            key = alias.removeprefix("optionsNs.")
            alias = str((context.options or {}).get(key, alias))
        model_id = self._resolve_model_alias(step.model_ref, context)

        decompose_math = step.get_domain_field("decompose_compound_math", True)
        gate_domains: set[str] = {"general"}
        if decompose_math:
            gate_domains.add("math")

        prompt_ref_classify = str(
            step.get_domain_field("prompt_ref_atomicity_classify")
            or "consensus.v7.classify_atomicity"
        )
        prompt_ref_decompose = str(
            step.get_domain_field("prompt_ref_decompose_compound")
            or "consensus.v7.decompose_general_compound"
        )

        step_chunk = (
            context.pipeline.options.get("atomicity_chunk_size")
            or step.get_domain_field("chunk_size")
            or context.pipeline.options.get("default_chunk_size")
        )
        if step_chunk is None:
            raise ValueError(
                "Chunk size required: set pipeline options default_chunk_size "
                "or atomicity_chunk_size, or step domain field chunk_size"
            )
        step_chunk = int(step_chunk)

        registry = context._registry
        model_config = registry.get_model_config(
            alias,
            domain=context.pipeline.domain,
            search_path=context.pipeline.source_search_path,
        )
        model_chunk = get_execution_config(model_config).chunk_size
        chunk_size = min(step_chunk, model_chunk)

        claims, compound_details = await atomicity_gate_decompose(
            handler=self,
            claims=claims,
            model_id=model_id,
            step=step,
            context=context,
            prompt_ref_classify=prompt_ref_classify,
            prompt_ref_decompose=prompt_ref_decompose,
            domains=frozenset(gate_domains),
            classify_chunk_size=chunk_size,
        )

        return StepOutput(
            raw="",
            json={"claims": claims, "compound_details": compound_details},
        )

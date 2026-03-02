"""Assign each claim to a verification domain (math, medical, or general).

Takes the flat claim list from decompose and asks an LLM to label each
claim's domain.  The domain tag determines how the claim is verified
downstream:

- **math** claims are routed to a specialist math model in domain_verify
  and may receive authority verdicts that bypass general voting.
- **medical** claims are routed to a medical authority model when one is
  configured in domain_verifiers; otherwise fall through to general voting.
  This routing is a no-op in v7 (no medical verifier configured) and is
  forward-compatible with v8.0, which adds a medical authority model.
- **general** claims flow through cross-model verification in
  verify_general, where multiple models independently vote.

Domain classification must happen before atomicity_gate so that
compound decomposition can be domain-aware (e.g. decomposing math
compounds differently from general ones).

Outputs:
    json.claims — same claim list with ``domain`` field populated
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

from systems.pipeline.core.execution.chunked.model_config import get_execution_config
from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

from ..._lib._chain_utils import classify_claims

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)


class ClassifyDomainHandler(BaseHandler):
    """Label each claim as math, medical, or general to control routing.

    Claims tagged ``math`` are sent to domain_verify's specialist model;
    ``medical`` claims are routed to a medical authority model if one is
    configured (v8.0+), otherwise fall through to general voting;
    ``general`` claims go through cross-model majority voting.
    """

    step_type: str = "consensus_classify_domain_v8_0"

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

        prompt_ref = str(step.get_domain_field("prompt_ref_classify") or "")
        step_chunk = (
            context.pipeline.options.get("classify_chunk_size")
            or step.get_domain_field("chunk_size")
            or context.pipeline.options.get("default_chunk_size")
        )
        if step_chunk is None:
            raise ValueError(
                "Chunk size required: set pipeline options default_chunk_size "
                "or classify_chunk_size, or step domain field chunk_size"
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

        claims = await classify_claims(
            self,
            claims,
            model_id,
            step,
            context,
            prompt_ref,
            chunk_size=chunk_size,
        )

        return StepOutput(raw="", json={"claims": claims})

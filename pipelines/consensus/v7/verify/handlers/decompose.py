"""First verification step: break a model's answer into atomic claims.

Sends the raw answer text and the original question to an LLM with a
decomposition prompt. The model returns a list of self-contained
factual statements (claims), each tagged with a statement_id and
mapped back to the source sentence in the answer (sentence provenance).

The resulting claim list is the unit of verification for every
downstream step — classify_domain, atomicity_gate, domain_verify,
verify_general, and filter_threshold all operate on these claims.

Outputs:
    json.claims          — list of claim dicts (statement_id, text, ...)
    json.answer_sentences — original answer split into sentences
"""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

from ...shared._chain_utils import decompose_answer

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)


class DecomposeHandler(BaseHandler):
    """Break answer text into atomic claims with sentence provenance.

    Each claim is a self-contained factual statement that can be
    independently verified.  Downstream steps classify, route, and
    vote on these claims to decide which survive into the final answer.
    """

    step_type: str = "consensus_decompose_v7"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        resolver = NamespaceResolver(context)
        answer = str(
            self._resolve_input(resolver, step, "answer", step.handler_inputs) or ""
        )
        question = str(
            self._resolve_input(resolver, step, "question", step.handler_inputs) or ""
        )

        if not answer:
            logger.error("Step '%s': empty answer input", step.id)
            return StepOutput(raw="", json={"claims": [], "answer_sentences": []})

        if not step.model_ref:
            raise ValueError(f"Step '{step.id}' missing model_ref")
        model_id = self._resolve_model_alias(step.model_ref, context)

        prompt_ref = self._require_domain_field(step, "prompt_ref_decompose")
        claims, answer_sentences = await decompose_answer(
            handler=self,
            answer_text=answer,
            question=question,
            decompose_model_id=model_id,
            step=step,
            context=context,
            prompt_ref=prompt_ref,
        )

        return StepOutput(
            raw="",
            json={"claims": claims, "answer_sentences": answer_sentences},
        )

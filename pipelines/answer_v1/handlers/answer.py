"""Generate step that strips a leading <think>...</think> block from LLM output.

Used by the answer_v1 pipeline so thinking tokens from phi4 (or similar) are
not included in the final answer. Only the first such block at the start
of the response is removed; content after </think> is kept.
"""

from __future__ import annotations

import dataclasses
import time
from typing import TYPE_CHECKING, Any, override

from systems.pipeline.core.constants import RAG_NO_RESULTS_SENTINEL
from systems.pipeline.core.handlers.builtin import ModelCallResult
from systems.pipeline.core.handlers.generate import GenericGenerateHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)

THINK_OPEN = "<think>"
THINK_CLOSE = "</think>"


def strip_leading_think_block(text: str) -> str:
    """
    Remove a leading <think>...</think> block (inclusive, line-based).

    Only runs when the content begins with <think>: the first non-empty line
    must contain <think>. That line and every line until (and including) a
    line that contains </think> are removed; the remainder is returned stripped.
    Otherwise the original text is returned unchanged.
    """
    if not text or THINK_OPEN not in text:
        return text

    lines = text.splitlines()
    start_idx = -1
    for i, line in enumerate(lines):
        if line.strip():
            if THINK_OPEN in line:
                start_idx = i
            break  # first non-empty line seen; stop regardless

    if start_idx == -1:
        return text

    end_idx = -1
    for i in range(start_idx, len(lines)):
        if THINK_CLOSE in lines[i]:
            end_idx = i
            break

    if end_idx == -1:
        return text

    remainder = "\n".join(lines[end_idx + 1 :])
    return remainder.strip()


class AnswerGenerateHandler(GenericGenerateHandler):
    """
    Generate handler for answer_v1 that strips a leading <think>...</think> block.

    Registered for domain answer_v1, step_type "generate", so it is used
    only for the answer step of the RAG answer pipeline.
    """

    step_type = "generate"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        """Execute answer step, honouring pipeline_options.model when supplied.

        Empty-context guard: if the upstream get_context step returned the
        RAG_NO_RESULTS_SENTINEL, skip the LLM call and return a canned
        not-grounded response. Coupling: depends on the step being named
        'get_context' in answer-v1.yaml.

        RAG_NO_RETRIEVAL_SENTINEL (needs_retrieval=false / conversational query)
        is intentionally not blocked — conversational queries may still use
        model knowledge.
        """
        # Only bypass model call for the answer step — relevance_check must always
        # run so it can classify the empty context as relevant=false.
        get_context_out = context.get_output("get_context")
        if (
            step.name != "relevance_check"
            and get_context_out is not None
            and get_context_out.raw == RAG_NO_RESULTS_SENTINEL
        ):
            return StepOutput(
                raw=(
                    "I don't have indexed documents covering this question. "
                    "Try asking about topics covered in the project documentation."
                ),
                json={"fallback": True, "reason": "no_retrieved_documents"},
            )

        override_model: str | None = (context.options or {}).get("model")
        if override_model:
            return await self._execute_with_model_override(
                step, context, override_model
            )
        return await super().execute(step, context)

    async def _execute_with_model_override(
        self,
        step: StepConfig,
        context: PipelineContext,
        model_id: str,
    ) -> StepOutput:
        """Run the answer step using an explicitly supplied model ID.

        Bypasses registry.get_model_config() — model_id is treated as already
        resolved and forwarded to Stargate routing verbatim.
        ∀ model_id supplied here: routing responsibility transferred to Stargate.
        """
        start_time = time.time()
        registry = context._registry
        prompt_config = registry.get_prompt(step.prompt_ref)
        user_prompt = self._render_user_prompt(prompt_config, step, context)

        resolved_config: dict[str, Any] = {
            "model_id": model_id,
            "system_prompt": prompt_config.system_prompt or "",
            "temperature": step.generation_parameters.get("temperature"),
            "max_tokens": self._resolve_max_tokens(step, context),
            "json_schema": step.generation_parameters.get("response_format", {}).get(
                "schema"
            ),
        }

        logger.debug("answer step: pipeline_options.model override → %s", model_id)

        call_result = await self._call_model(
            model_id,
            user_prompt,
            step,
            context,
            resolved_config["system_prompt"],
            temperature=resolved_config["temperature"],
            max_tokens=resolved_config["max_tokens"],
            json_schema=resolved_config["json_schema"],
            model_id_is_resolved=True,
        )

        latency_ms = (time.time() - start_time) * 1000
        return self._build_step_output(
            call_result, resolved_config, latency_ms, step.id
        )

    @override
    def _build_step_output(
        self,
        call_result: ModelCallResult,
        resolved_config: dict[str, Any],
        latency_ms: float,
        step_id: str,
        source_provenance: dict[str, Any] | None = None,
    ) -> StepOutput:
        """
        Strip any leading <think>…</think> block from the model response, then
        delegate to the base class to build and return a StepOutput.
        """
        stripped = strip_leading_think_block(call_result.content)
        if stripped != call_result.content:
            logger.debug(
                "Step '%s': stripped leading <think>...</think> block (%d → %d chars)",
                step_id,
                len(call_result.content),
                len(stripped),
            )
            wrapped = dataclasses.replace(call_result, content=stripped)
        else:
            wrapped = call_result
        return super()._build_step_output(
            wrapped,
            resolved_config,
            latency_ms,
            step_id,
            source_provenance=source_provenance,
        )

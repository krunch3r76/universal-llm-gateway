"""Generate step that strips a leading <think>...</think> block from LLM output.

Used by the answer_v1 pipeline so thinking tokens from phi4 (or similar) are
not included in the final answer. Only the first such block at the start
of the response is removed; content after </think> is kept.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any, override

from systems.pipeline.core.handlers.builtin import ModelCallResult
from systems.pipeline.core.handlers.generate import GenericGenerateHandler
from universal_logging import get_logger

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext, StepOutput
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
    start_idx: int | None = None
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        if THINK_OPEN in line:
            start_idx = i
            break
        return text  # First non-empty line does not contain <think>
    if start_idx is None:
        return text
    end_idx: int | None = None
    for i in range(start_idx, len(lines)):
        if THINK_CLOSE in lines[i]:
            end_idx = i
            break
    if end_idx is None:
        return text
    after = lines[end_idx + 1 :]
    remainder = "\n".join(after)
    return remainder.strip()


class AnswerGenerateHandler(GenericGenerateHandler):
    """
    Generate handler for answer_v1 that strips a leading <think>...</think> block.

    Registered for domain answer_v1, step_type "generate", so it is used
    only for the answer step of the RAG answer pipeline.
    """

    step_type = "generate"

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
        return super()._build_step_output(
            wrapped,
            resolved_config,
            latency_ms,
            step_id,
            source_provenance=source_provenance,
        )

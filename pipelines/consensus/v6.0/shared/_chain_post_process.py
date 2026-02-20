"""
Post-processing: synthesize a clean answer from accepted facts.

Optionally receives an original answer as scaffolding context — the
synthesis prompt can reference it for structure and style while only
including content from verified facts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.builtin import BaseHandler
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)

_MIN_SYNTHESIS_TOKENS: int = 4096
_TOKENS_PER_FACT: int = 150


def _format_rejected_blacklist(rejected_claims: list[dict[str, Any]]) -> str:
    """Format rejected claims as a blacklist section for the synthesis prompt.

    Returns empty string when there are no rejections, allowing the prompt
    template to collapse the section via Jinja2 conditionals or safe-mode
    empty-string substitution.
    """
    texts = [c.get("text", "") for c in rejected_claims if c.get("text")]
    if not texts:
        return ""
    lines = "\n".join(f"- {t}" for t in texts)
    return (
        "REJECTED CLAIMS (BLACKLIST — these were disproven during verification):\n"
        f"{lines}\n"
        "You MUST NOT include any of the above claims or restate them in any form."
    )


async def post_process_synthesize(
    handler: BaseHandler,
    accepted_facts: list[dict[str, Any]],
    question: str,
    model_id: str,
    step: StepConfig,
    context: PipelineContext,
    prompt_ref: str,
    original_answer: str = "",
    rejected_claims: list[dict[str, Any]] | None = None,
) -> str:
    """Synthesize a new answer from accepted facts, optionally scaffolded by an original answer."""
    if not accepted_facts:
        logger.error(
            "Step '%s': cannot synthesize with zero accepted facts. "
            "All claims were rejected during verification.",
            step.id,
        )
        raise ValueError(
            f"Step '{step.id}': synthesize mode requires at least one accepted fact"
        )

    numbered_facts = "\n".join(
        f"[{i}] {fact.get('text', '')}"
        for i, fact in enumerate(accepted_facts, start=1)
        if fact.get("text")
    )

    if not numbered_facts.strip():
        logger.error(
            "Step '%s': accepted_facts list is non-empty but contains no valid text",
            step.id,
        )
        raise ValueError(
            f"Step '{step.id}': accepted_facts contains no valid text content"
        )

    rejected_blacklist = _format_rejected_blacklist(rejected_claims or [])

    rendered = handler._render_prompt(
        prompt_ref,
        {
            "question": question,
            "numbered_facts": numbered_facts,
            "original_answer": original_answer,
            "rejected_blacklist": rejected_blacklist,
            "fact_count": str(len(accepted_facts)),
        },
        context,
        safe=True,
    )

    gen_params = step.generation_parameters or {}
    dynamic_budget = max(_MIN_SYNTHESIS_TOKENS, _TOKENS_PER_FACT * len(accepted_facts))
    call_result = await handler._call_model(
        model_id,
        rendered.user_prompt,
        step,
        context,
        system_prompt=rendered.system_prompt,
        temperature=gen_params.get("temperature", 0.3),
        max_tokens=handler._resolve_max_tokens(
            step, context, handler_default=dynamic_budget
        ),
        call_label="post_process",
    )

    if call_result.finish_reason == "length":
        logger.warning(
            "Post process synthesize: truncated at %d tokens — retrying with 2× budget",
            call_result.completion_tokens,
        )
        retry_budget = (call_result.completion_tokens or dynamic_budget) * 2
        call_result = await handler._call_model(
            model_id,
            rendered.user_prompt,
            step,
            context,
            system_prompt=rendered.system_prompt,
            temperature=gen_params.get("temperature", 0.3),
            max_tokens=retry_budget,
            call_label="post_process_retry",
        )
        if call_result.finish_reason == "length":
            logger.warning(
                "Post process synthesize: still truncated after retry (%d tokens). "
                "Answer may be incomplete.",
                call_result.completion_tokens,
            )

    logger.info(
        "Step '%s': synthesized from %d accepted facts",
        step.id,
        len(accepted_facts),
    )
    return call_result.content.strip()

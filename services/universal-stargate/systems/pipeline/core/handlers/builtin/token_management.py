"""
Token budget resolution and pre-flight context feasibility checks.

_resolve_max_tokens / _constrained_tokens delegate to token_resolution.py
(the canonical token budget logic shared with the executor). They exist here
so BaseHandler subclasses access them through a stable internal API without
importing from token_resolution directly.

_check_context_feasibility is a pre-flight guard: it estimates prompt token
count via a chars/4 heuristic and compares against the model's known context
window. Fail-open on missing metadata so pipelines don't break when model
metadata isn't available (e.g., newly registered models, test runs).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ..protocol import PipelineContext

if TYPE_CHECKING:
    from ...schemas import StepConfig


def _resolve_max_tokens(
    step: StepConfig,
    context: PipelineContext,
    *,
    handler_default: int | None = None,
) -> int | None:
    """Resolve the effective max_tokens for a step call.

    Applies the token_defaults hierarchy (step → pipeline → handler default)
    and then the constrained_multiplier when expansion_safe=false — ensuring
    sub-calls in constrained contexts don't exceed their epistemic budget.
    Returns None when no constraint applies (model uses its own default).
    """
    from ..token_resolution import resolve_max_tokens

    return resolve_max_tokens(step, context, handler_default=handler_default)


def _constrained_tokens(
    base: int,
    context: PipelineContext,
) -> int:
    """Scale a token budget by the pipeline's constrained_multiplier.

    Used by handlers that make multiple internal sub-calls (e.g., map steps,
    verification loops) where each call must stay within a fraction of the
    total budget to prevent one sub-call from consuming all available tokens.
    """
    from ..token_resolution import constrained_tokens

    return constrained_tokens(base, context)


def _check_context_feasibility(
    resolved_model_id: str,
    messages: list[dict[str, str]],
    step: StepConfig,
    context: PipelineContext,
    *,
    system_prompt: str | None = None,
    user_prompt: str = "",
    publish_event: Callable[[PipelineContext, Any], None] | None = None,
) -> None:
    """Pre-flight: does the assembled prompt plausibly fit the model's context?

    Uses a chars/4 heuristic (overestimates for English) and compares
    against effective_context_per_slot (or total context_length).
    On mismatch: emits ContextExceeded (recorder) + bus signal, emits a
    failed ModelInvocation (preserving the 1:1 invariant), then raises
    ContextExceededError.  No-ops when metadata is unavailable (fail-open).

    Args:
        publish_event: Optional callback ``(context, event) → None`` for
            publishing bus-level events. Pass ``handler._publish_bus_event``
            from the calling BaseHandler instance.
    """
    from systems.routing.selection.catalog import get_model_context_metadata

    proxy = getattr(context, "_proxy", None)
    if not proxy:
        return

    gw_mgr = getattr(proxy, "gateway_manager", None)
    fed_mgr = getattr(proxy, "federated_manager", None)
    if not gw_mgr and not fed_mgr:
        return

    metadata = get_model_context_metadata(gw_mgr, fed_mgr)
    model_meta = metadata.get(resolved_model_id)
    if not model_meta:
        return

    total_chars = sum(len(m.get("content", "")) for m in messages)
    estimated_tokens = total_chars // 4

    ctx_len = model_meta.get("context_length", 0)
    eff_ctx = model_meta.get("effective_context_per_slot", ctx_len)
    if eff_ctx <= 0:
        return

    if estimated_tokens > eff_ctx:
        from ...dag import ContextExceededError
        from ...events.inference import ContextExceeded, ModelInvocation
        from ...events.step import StepContextExceeded

        recorder = context.recorder
        if recorder:
            recorder.emit(
                ContextExceeded(
                    step_name=step.name,
                    model_id=resolved_model_id,
                    estimated_tokens=estimated_tokens,
                    context_length=ctx_len,
                    effective_context_per_slot=eff_ctx,
                    prompt_chars=total_chars,
                )
            )

        error_msg = (
            f"context_exceeded: ~{estimated_tokens} tokens vs {eff_ctx} context window"
        )

        # Preserve ModelInvocation 1:1 invariant
        if recorder:
            recorder.emit(
                ModelInvocation(
                    step_name=step.name,
                    model_id=resolved_model_id,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    error=error_msg,
                    success=False,
                )
            )

        # Bus signal (Event, not PipelineEvent) — via injected callback
        bus_event = StepContextExceeded(
            pipeline_id=context.pipeline.name,
            execution_id=context.execution_id,
            step_name=step.name,
            model_id=resolved_model_id,
            estimated_tokens=estimated_tokens,
            context_length=ctx_len,
            effective_context_per_slot=eff_ctx,
            prompt_chars=total_chars,
        )
        if publish_event:
            publish_event(context, bus_event)

        raise ContextExceededError(
            step_name=step.name,
            model_id=resolved_model_id,
            estimated_tokens=estimated_tokens,
            context_length=eff_ctx,
            prompt_chars=total_chars,
        )

"""Post-loop observability emission for ``frontier_dispatch_v1``.

Keeps the main handler under the 400-SLOC soft limit while adding the
Phase-1 hoisted signals. One entry point, ``emit_post_loop_observability``,
orchestrates both detectors and their Stargate event-factory translations:

- ``pipeline.frontier.dispatch.output.short`` — fires when a team-seat
  dispatch returns ``output_tokens < 500`` (silent-failure anomaly).
- ``pipeline.frontier.dispatch.termination.shadow`` — fires when Gemini's
  thought trace looks like a silent refusal / loop / MAX_TOKENS-on-thought.

The handler calls this unconditionally after a non-exhausted success;
persona-free dispatches short-circuit here (``agent is None``) since both
anomalies are only meaningful for team/full boot-level equivalents.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from frontier_observability import (
    TerminationShadowDetector,
    detect_output_short,
)

from ..events.dispatch import (
    PipelineFrontierDispatchOutputShort,
    PipelineFrontierDispatchTerminationShadow,
)

if TYPE_CHECKING:
    from agent_seat.native_loop import NativeLoopResult

    from .protocol import PipelineContext

# Module-level detector — stateless, safe to share across dispatches.
_TERMINATION_DETECTOR = TerminationShadowDetector()

# Boot-level equivalent for a hydrated persona dispatch. The pipeline handler
# has no multi-tier boot concept (team vs full); ``agent is not None`` gates
# the emission, so the detector sees the level that matches
# ``_frontier_core``'s ``boot='team'`` path.
_PIPELINE_BOOT_LEVEL = "team"


def emit_post_loop_observability(
    *,
    context: PipelineContext,
    publish: Any,
    agent: str | None,
    model: str,
    result: NativeLoopResult,
) -> None:
    """Fire the Phase-1 hoisted observability signals on success.

    No-op when ``agent is None`` (persona-free dispatch) — both detectors
    internally gate on team/full boot equivalence. Both helpers fall
    through quietly when their own gates (provider, substrate, threshold)
    don't match, so this can be called unconditionally on the success path.
    """
    if agent is None:
        return
    output_tokens = int(result.usage.get("output_tokens", 0))
    _emit_output_short(
        publish=publish,
        execution_id=context.execution_id,
        agent=agent,
        model=model,
        provider=result.provider,
        output_tokens=output_tokens,
        tool_calls_made=result.tool_calls_made,
        finish_reason=result.finish_reason,
        block_reason=result.block_reason,
        content=result.content,
    )
    _emit_termination_shadow(
        publish=publish,
        execution_id=context.execution_id,
        agent=agent,
        model=model,
        provider=result.provider,
        output_tokens=output_tokens,
        finish_reason=result.finish_reason,
        block_reason=result.block_reason,
        reasoning=result.reasoning,
        content=result.content,
    )


def _emit_output_short(
    *,
    publish: Any,
    execution_id: str,
    agent: str,
    model: str,
    provider: str,
    output_tokens: int,
    tool_calls_made: int,
    finish_reason: str | None,
    block_reason: str | None,
    content: str | None,
) -> None:
    payload = detect_output_short(
        boot_level=_PIPELINE_BOOT_LEVEL,
        output_tokens=output_tokens,
        tool_calls_made=tool_calls_made,
        finish_reason=finish_reason,
        block_reason=block_reason,
        content=content,
    )
    if payload is None:
        return
    publish(
        PipelineFrontierDispatchOutputShort(
            agent=agent,
            execution_id=execution_id,
            model=model,
            provider=provider,
            boot_level=payload.boot_level,
            output_tokens=payload.output_tokens,
            tool_calls_made=payload.tool_calls_made,
            finish_reason=payload.finish_reason,
            block_reason=payload.block_reason,
            content_preview=payload.content_preview,
        )
    )


def _emit_termination_shadow(
    *,
    publish: Any,
    execution_id: str,
    agent: str,
    model: str,
    provider: str,
    output_tokens: int,
    finish_reason: str | None,
    block_reason: str | None,
    reasoning: Any,
    content: str | None,
) -> None:
    payload = _TERMINATION_DETECTOR.detect(
        provider=provider,
        boot_level=_PIPELINE_BOOT_LEVEL,
        reasoning=reasoning,
        content=content,
        finish_reason=finish_reason,
        output_tokens=output_tokens,
    )
    if payload is None:
        return
    evidence_dicts = [
        {"kind": e.kind, "score": e.score, "excerpt": e.excerpt}
        for e in payload.evidence
    ]
    publish(
        PipelineFrontierDispatchTerminationShadow(
            agent=agent,
            execution_id=execution_id,
            model=model,
            provider=provider,
            boot_level=_PIPELINE_BOOT_LEVEL,
            output_tokens=output_tokens,
            finish_reason=finish_reason,
            block_reason=block_reason,
            reason=payload.reason,
            confidence=payload.confidence,
            evidence=evidence_dicts,
            suggested_next_action=payload.suggested_next_action,
            trace_visibility=payload.trace_visibility,
            generate_id=payload.generate_id,
            detector=payload.detector,
        )
    )

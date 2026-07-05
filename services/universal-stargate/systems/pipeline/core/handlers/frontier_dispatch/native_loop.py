"""Native tool-loop execution and terminal-event emission.

Builds the streaming closures, emits ``PipelineFrontierDispatchStarted``, runs
the bounded native tool loop, and emits the terminal events
(``Exhausted`` / ``Completed`` / ``EmptyCompletion``) with the exact branch
order and empty-content sub-routing of the monolith. Raises
``FrontierDispatchExhaustedError`` or ``EmptyCompletionError`` on empty terminal
content. Returns a :class:`LoopOutcome` for the completion phase when terminal
content is non-empty.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agent_seat.native_loop import NativeLoopResult, run_native_tool_loop
from llm_adapters._mcp_entry import RemoteMcpEnvMissingError

from ...events.dispatch import (
    PipelineFrontierDispatchCompleted,
    PipelineFrontierDispatchEmptyCompletion,
    PipelineFrontierDispatchExhausted,
    PipelineFrontierDispatchRemoteMcpMisconfigured,
    PipelineFrontierDispatchStarted,
)
from ...execution.errors import (
    EmptyCompletionError,
    FrontierDispatchExhaustedError,
)
from .streaming import (
    REMOTE_MCP_OVERALL_TIMEOUT_S,
    build_cancel_check,
    build_in_process_sender,
    build_on_tool_event,
)

if TYPE_CHECKING:
    from llm_adapters import FrontierRequest

    from ..protocol import PipelineContext
    from ..schemas import StepConfig
    from .admission_gate import AdmissionResult
    from .handler import FrontierDispatchHandler


# Hard wall-clock backstop for a remote-MCP dispatch, applied at the tool-loop
# level (task cancellation) on top of the graceful SSE-level
# ``REMOTE_MCP_OVERALL_TIMEOUT_S``. The accumulator ceiling resets on every SSE
# frame and bounds the stream when frames behave; but a server-side MCP loop
# that makes zero token progress was observed to outrun it in production (exec
# 012e5e1e, thread 1652: ~449s with 0 tokens and no terminal event, past the
# 300s SSE ceiling). ``asyncio.timeout`` does not depend on frame arrival, so it
# converts any such silent hang into a loud terminal error regardless of how the
# provider holds the HTTP response open. Sized a grace margin above the SSE
# ceiling so the graceful SSE timeout (specific error) wins whenever it can fire.
REMOTE_MCP_LOOP_GRACE_S = 30.0


def _resolve_remote_mcp_ceiling(opts: dict[str, Any]) -> float:
    """Loop-level wall-clock ceiling for a remote-MCP dispatch.

    Honors a caller ``timeout_seconds`` (legitimate long consults) and otherwise
    falls back to ``REMOTE_MCP_OVERALL_TIMEOUT_S``; a grace margin is added so
    the SSE-level ceiling gets first chance to surface its specific error.
    """
    raw = opts.get("timeout_seconds")
    base = (
        float(raw)
        if isinstance(raw, int | float) and raw > 0
        else REMOTE_MCP_OVERALL_TIMEOUT_S
    )
    return base + REMOTE_MCP_LOOP_GRACE_S


async def _run_loop_bounded(
    *,
    ceiling_s: float | None,
    model: str,
    req: FrontierRequest,
    send_native: Any,
    agent: str | None,
    max_turns: int,
    on_tool_event: Any,
    cancel_check: Any,
) -> NativeLoopResult:
    """Run the native tool loop, bounding remote-MCP dispatches by wall-clock.

    When ``ceiling_s`` is ``None`` (client-side loop / plain generate) the loop
    runs unbounded — only remote-MCP dispatches carry the silent-hang risk this
    backstop addresses. A breach raises ``RuntimeError`` (loud terminal →
    ``pipeline_execution_failed``), mirroring the SSE-liveness-failure shape.
    """
    if ceiling_s is None:
        return await run_native_tool_loop(
            model=model,
            req=req,
            send_native=send_native,
            agent=agent,
            max_turns=max_turns,
            on_tool_event=on_tool_event,
            cancel_check=cancel_check,
        )
    try:
        async with asyncio.timeout(ceiling_s):
            return await run_native_tool_loop(
                model=model,
                req=req,
                send_native=send_native,
                agent=agent,
                max_turns=max_turns,
                on_tool_event=on_tool_event,
                cancel_check=cancel_check,
            )
    except TimeoutError as exc:
        raise RuntimeError(
            f"remote-MCP dispatch exceeded wall-clock ceiling ({ceiling_s:.0f}s) "
            "with no terminal result — bounding a zero-progress server-side MCP "
            "hang (decision:remote-mcp-dispatch-overall-timeout)"
        ) from exc


@dataclass
class LoopOutcome:
    """Result of the native tool loop plus derived terminal metadata.

    ``result`` is the raw NativeLoopResult; the scalar terminal fields are
    extracted once here so the completion phase does not re-``getattr`` them.
    Only constructed when the loop produced non-empty terminal content — empty
    content raises in :func:`run_dispatch_loop` before any LoopOutcome is built.
    """

    result: NativeLoopResult
    latency_ms: float
    finish_reason: Any
    block_reason: Any
    exhaustion_summary: Any


async def run_dispatch_loop(
    handler: FrontierDispatchHandler,
    step: StepConfig,
    context: PipelineContext,
    admission: AdmissionResult,
    req: FrontierRequest,
) -> LoopOutcome:
    """Run the bounded native tool loop and emit Started + terminal events.

    ``handler`` is accepted for signature parity with the other phase functions
    and future hook needs; the loop body publishes via ``admission.publish``.
    """
    agent = admission.agent
    model = admission.model
    model_entity_id = admission.model_entity_id
    opts = admission.opts
    publish = admission.publish

    cancel_check = build_cancel_check(context)
    send_native = build_in_process_sender(
        context,
        step.id,
        agent,
        publish=publish,
        cancel_check=cancel_check,
        default_overall_timeout=(
            REMOTE_MCP_OVERALL_TIMEOUT_S if admission.remote_mcp else None
        ),
    )
    on_tool_event = build_on_tool_event(context, agent, publish=publish)

    publish(
        PipelineFrontierDispatchStarted(
            execution_id=context.execution_id,
            agent=agent,
            model=model,
            model_entity_id=model_entity_id,
            provider=admission.provider,
            boot_level="team" if agent else "none",
            remote_mcp=admission.remote_mcp,
            op=opts.get("op", ""),
            endpoint_request_id=opts.get("_endpoint_request_id"),
        )
    )

    call_start = time.monotonic()
    remote_mcp_ceiling = (
        _resolve_remote_mcp_ceiling(opts) if admission.remote_mcp else None
    )
    try:
        result: NativeLoopResult = await _run_loop_bounded(
            ceiling_s=remote_mcp_ceiling,
            model=model,
            req=req,
            send_native=send_native,
            agent=agent,
            max_turns=admission.max_turns,
            on_tool_event=on_tool_event,
            cancel_check=cancel_check,
        )
    except RemoteMcpEnvMissingError as exc:
        # resolve_mcp_env() raises when MCP_PUBLIC_URL/MCP_AUTH_TOKEN is unset
        # in the Stargate container env. Emit the dedicated signal before
        # bubbling, so pipeline_execution_failed carries a
        # structural-misconfiguration trail.
        publish(
            PipelineFrontierDispatchRemoteMcpMisconfigured(
                execution_id=context.execution_id,
                agent=agent,
                model=model,
                model_entity_id=model_entity_id,
                reason=str(exc),
            )
        )
        raise
    latency_ms = (time.monotonic() - call_start) * 1000.0

    finish_reason = getattr(result, "finish_reason", None)
    block_reason = getattr(result, "block_reason", None)
    exhaustion_summary = getattr(result, "exhaustion_summary", None)
    if isinstance(exhaustion_summary, dict):
        exhaustion_summary = {
            **exhaustion_summary,
            "execution_id": context.execution_id,
        }
    if result.exhausted:
        publish(
            PipelineFrontierDispatchExhausted(
                agent=agent,
                execution_id=context.execution_id,
                turns_used=result.turns_used,
                tool_calls_made=result.tool_calls_made,
                provider=result.provider,
                model_entity_id=model_entity_id,
                op=opts.get("op", ""),
                finish_reason=finish_reason,
                block_reason=block_reason,
                enforcement="client",
                exhaustion_summary=exhaustion_summary,
            )
        )
        if not (result.content or "").strip():
            raise FrontierDispatchExhaustedError(
                execution_id=context.execution_id,
                agent=agent,
                model=model,
                provider=result.provider,
                turns_used=result.turns_used,
                tool_calls_made=result.tool_calls_made,
                finish_reason=finish_reason,
                block_reason=block_reason,
                exhaustion_summary=exhaustion_summary,
            )
    else:
        publish(
            PipelineFrontierDispatchCompleted(
                agent=agent,
                execution_id=context.execution_id,
                turns_used=result.turns_used,
                tool_calls_made=result.tool_calls_made,
                reasoning_present=result.reasoning is not None,
                prompt_tokens=result.usage.get("input_tokens", 0),
                completion_tokens=result.usage.get("output_tokens", 0),
                provider=result.provider,
                model_entity_id=model_entity_id,
                op=opts.get("op", ""),
                finish_reason=finish_reason,
                block_reason=block_reason,
            )
        )
        # F3: detect silent empty-completion on the non-exhausted branch and
        # convert terminal state to failed. Exhausted empty content is handled
        # above as a distinct tool-loop budget failure. Originally surfaced by
        # Orion execution d65c723b (Cortex assertion 7903).
        #
        # Tool-only completion: MCP-heavy turns may finish with tool activity
        # but no final assistant text. Downstream ``archive_assistant_turn_v1``
        # synthesizes archive text from the tool-call trace — do not fail the
        # respond step (cortex-chat-openai Phase E gap, assertion 12167).
        if not (result.content or "").strip() and result.tool_calls_made > 0:
            return LoopOutcome(
                result=result,
                latency_ms=latency_ms,
                finish_reason=finish_reason,
                block_reason=block_reason,
                exhaustion_summary=exhaustion_summary,
            )
        #
        # Sub-case: a provider-managed tool loop (remote-MCP, or any loop where
        # the provider stops on its own ceiling) returns ``content=""`` with
        # ``finish_reason in {tool_calls, length}``. The native loop never sets
        # ``result.exhausted`` (it only saw one provider round-trip), so without
        # finish_reason inspection this looks like a generic empty completion.
        # Re-route to the ``exhausted`` signal with ``enforcement="provider"``
        # so traces are queryable as ceiling hits — observed on execution
        # ``e07481c4`` (todo:frontier-dispatch-empty-content-exhaustion).
        if not (result.content or "").strip():
            ceiling_finish_reasons = {"tool_calls", "tool_use", "length"}
            if finish_reason in ceiling_finish_reasons:
                publish(
                    PipelineFrontierDispatchExhausted(
                        agent=agent,
                        execution_id=context.execution_id,
                        turns_used=result.turns_used,
                        tool_calls_made=result.tool_calls_made,
                        provider=result.provider,
                        model_entity_id=model_entity_id,
                        op=opts.get("op", ""),
                        finish_reason=finish_reason,
                        block_reason=block_reason,
                        enforcement="provider",
                        exhaustion_summary=exhaustion_summary,
                    )
                )
            else:
                publish(
                    PipelineFrontierDispatchEmptyCompletion(
                        execution_id=context.execution_id,
                        agent=agent,
                        model=model,
                        model_entity_id=model_entity_id,
                        provider=result.provider,
                        turns_used=result.turns_used,
                        tool_calls_made=result.tool_calls_made,
                        finish_reason=finish_reason,
                        block_reason=block_reason,
                    )
                )
            raise EmptyCompletionError(
                execution_id=context.execution_id,
                agent=agent,
                model=model,
                provider=result.provider,
                turns_used=result.turns_used,
                finish_reason=finish_reason,
            )

    return LoopOutcome(
        result=result,
        latency_ms=latency_ms,
        finish_reason=finish_reason,
        block_reason=block_reason,
        exhaustion_summary=exhaustion_summary,
    )

"""Provider-native tool-use loop — transport-agnostic.

Runs a bounded multi-turn conversation against a provider-native endpoint
(Anthropic messages, OpenAI/xAI responses, Google generateContent), where
the model can request tool calls that this loop executes locally (via
``libs/agent_seat/executor.execute_tool``) and appends to the conversation
via the adapter's ``append_tool_round``.

Key design: the HTTP transport is injected via ``send_native`` so the same
loop serves both in-process (Stargate pipeline handler) and HTTP-hop (MCP
frontier_dispatch, tests) callers. The provider-native path string is
resolved via ``NATIVE_PATHS`` and passed to ``send_native``; the caller
decides what URL that path maps to.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from llm_adapters import (
    FrontierRequest,
    effective_provider_for_model,
    resolve_llm_adapter,
)
from model_id import ModelId

from agent_seat.context import bind_active_persona, reset_active_persona
from agent_seat.executor import execute_tool
from agent_seat.tool_friction import ToolFrictionTracker

logger = logging.getLogger(__name__)


NATIVE_PATHS: dict[str, str] = {
    "anthropic": "/api/v1/providers/anthropic/messages",
    "xai": "/api/v1/providers/xai/responses",
    "openai": "/api/v1/providers/openai/responses",
    "chatgpt": "/api/v1/providers/openai/responses",
    "google": "/api/v1/providers/google/generateContent",
}


# Advisory text shown to the model when the tool-loop budget exhausts and the
# synthesis round fires. Designed to be neutral about WHY exhaustion happened
# (could be max_turns OR friction-tracker stop) and to signal clearly that no
# further tool calls are available — the model should now summarize from
# already-loaded context.
_EXHAUSTION_ADVISORY_TEXT = (
    "[Tool budget exhausted] Your client-side tool-call budget for this "
    "dispatch has been reached, and tool access is now disabled — no further "
    "tool calls will be available on this turn. Synthesize the best possible "
    "final answer from the information you have already gathered above. If "
    "the work is incomplete, say so explicitly and state what the next step "
    "would have been; do not request additional tools."
)


SendNativeFn = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
ToolEventFn = Callable[[str, dict[str, Any]], None]
CancelCheckFn = Callable[[], bool]


@dataclass(slots=True)
class NativeToolCall:
    """One tool call executed inside the native loop."""

    turn: int
    name: str
    arguments: dict[str, Any]
    result: str
    ok: bool
    elapsed_ms: float


@dataclass(slots=True)
class NativeLoopResult:
    """Terminal state of a native tool loop."""

    content: str
    reasoning: Any = None
    tool_calls: list[NativeToolCall] = field(default_factory=list)
    turns_used: int = 0
    exhausted: bool = False
    cancelled: bool = False
    usage: dict[str, Any] = field(default_factory=dict)
    finish_reason: str | None = None
    block_reason: str | None = None
    provider: str = ""
    raw: dict[str, Any] | None = None
    exhaustion_summary: dict[str, Any] | None = None
    synthesized: bool = False
    """True iff a synthesis round fired after tool-loop exhaustion and
    produced non-empty content. Lets downstream callers/telemetry tell
    apart ``exhausted-with-content`` (synthesis succeeded) from
    ``exhausted-with-content`` (model produced final text on the last
    in-budget turn). When True, ``content`` came from the synthesis
    round; otherwise ``content`` came from the last in-loop turn."""

    @property
    def tool_calls_made(self) -> int:
        return len(self.tool_calls)


def _normalize_tool_call(raw: Any) -> tuple[str, dict[str, Any], str]:
    """Return (name, args, call_id). Accepts Anthropic ``input``, Responses
    ``arguments`` (JSON string), Google ``input``/``arguments`` shapes.
    """
    if not isinstance(raw, dict):
        return "", {}, ""
    name = str(raw.get("name", "") or "")
    call_id = str(raw.get("id", "") or "")
    inp = raw.get("input")
    if isinstance(inp, dict):
        return name, inp, call_id
    raw_args = raw.get("arguments", "{}")
    if isinstance(raw_args, dict):
        return name, raw_args, call_id
    if isinstance(raw_args, str):
        try:
            parsed = json.loads(raw_args)
            return name, (parsed if isinstance(parsed, dict) else {}), call_id
        except json.JSONDecodeError:
            return name, {}, call_id
    return name, {}, call_id


async def _execute_tool_calls(
    tool_calls: list[dict[str, Any]],
    provider: str,
    turn: int,
    on_tool_event: ToolEventFn | None,
    *,
    friction: ToolFrictionTracker,
    max_turns: int,
) -> tuple[list[dict[str, Any]], list[NativeToolCall]]:
    """Execute tool_calls in parallel; return (adapter_tool_results, captured)."""

    async def _run_one(
        raw_call: dict[str, Any],
    ) -> tuple[dict[str, Any], NativeToolCall]:
        name, args, call_id = _normalize_tool_call(raw_call)
        start = time.monotonic()
        ok = True
        remaining_turns = max_turns - turn + 1
        skip = friction.should_skip(
            name,
            args,
            remaining_turns=remaining_turns,
        )
        if skip is not None:
            result_str = json.dumps(
                {
                    "error": skip.message,
                    "code": skip.reason,
                    "suggested_next_action": skip.suggested_next_action,
                }
            )
            ok = False
        else:
            try:
                result_str = await execute_tool(name, args)
                try:
                    parsed = json.loads(result_str) if result_str else {}
                    if isinstance(parsed, dict) and "error" in parsed:
                        ok = False
                except json.JSONDecodeError:
                    pass
            except Exception as exc:
                logger.error(
                    "native_loop tool %s (turn %d, provider %s) failed: %s",
                    name,
                    turn,
                    provider,
                    exc,
                )
                result_str = json.dumps({"error": f"tool execution failed: {exc}"})
                ok = False
        elapsed_ms = (time.monotonic() - start) * 1000.0

        tc = NativeToolCall(
            turn=turn,
            name=name,
            arguments=args,
            result=result_str,
            ok=ok,
            elapsed_ms=elapsed_ms,
        )
        friction.observe(tc)
        if on_tool_event is not None:
            try:
                signal = (
                    "pipeline.frontier.dispatch.tool.called"
                    if ok
                    else "pipeline.frontier.dispatch.tool.failed"
                )
                on_tool_event(
                    signal,
                    {
                        "tool_name": name,
                        "turn": turn,
                        "ok": ok,
                        "elapsed_ms": round(elapsed_ms, 1),
                        "provider": provider,
                        "arguments": args,
                        "full_error": {"message": result_str} if not ok else None,
                        "retry_count": 0,  # Can be incremented by caller if retry logic added
                    },
                )
            except Exception as cb_exc:
                logger.warning("on_tool_event callback failed: %s", cb_exc)

        adapter_result: dict[str, Any] = {
            "id": call_id,
            "name": name,
            "content": result_str,
        }
        return adapter_result, tc

    executed = await asyncio.gather(*(_run_one(tc) for tc in tool_calls))
    results = [e[0] for e in executed]
    captured = [e[1] for e in executed]
    return results, captured


async def run_native_tool_loop(
    *,
    model: str,
    req: FrontierRequest,
    send_native: SendNativeFn,
    agent: str | None = None,
    max_turns: int = 10,
    on_tool_event: ToolEventFn | None = None,
    cancel_check: CancelCheckFn | None = None,
) -> NativeLoopResult:
    """Run a bounded native-endpoint tool-use loop.

    Args:
        model: Full model id (e.g. ``openai/gpt-5.4``, ``anthropic/claude-opus-4-7``).
        req: ``FrontierRequest`` carrying messages, system, reasoning knobs, tools.
        send_native: Async ``(path, json_body) -> raw_response`` callable.
            Pipeline handler injects in-process ``CloudProxyClient``; MCP
            and tests inject httpx-based callables.
        agent: Dispatched persona slug (e.g. ``orion``). When set, nested
            ``agent_bus`` post/reply calls that omit ``from`` inherit this value.
        max_turns: Upper bound on model+tool rounds. ``exhausted=True`` in
            result if hit without terminal content.
        on_tool_event: Optional per-tool-call observability hook invoked as
            ``on_tool_event(signal, payload)``. Signals:
            ``pipeline.frontier.dispatch.tool.called`` (ok),
            ``pipeline.frontier.dispatch.tool.failed`` (error envelope).
        cancel_check: Optional ``() -> bool`` checked at each turn boundary.
            Returning True sets ``result.cancelled = True`` and terminates
            the loop gracefully.

    Returns:
        ``NativeLoopResult`` with terminal content, captured tool trace,
        usage, reasoning, raw response.

    Raises:
        ValueError if provider has no native path or no API key configured.
    """
    parsed = ModelId.parse(model)
    provider = effective_provider_for_model(parsed.provider)
    path = NATIVE_PATHS.get(provider)
    if not path:
        raise ValueError(
            f"No native path for provider {provider!r} (model={model!r}). "
            f"Known: {sorted(NATIVE_PATHS)}"
        )
    adapter = resolve_llm_adapter(parsed.provider)
    if adapter is None:
        raise ValueError(f"Provider {provider!r} not configured (missing API key).")
    if not hasattr(adapter, "build_frontier_request"):
        raise ValueError(
            f"Provider {provider!r} adapter does not support frontier requests."
        )

    _url, _headers, json_body = adapter.build_frontier_request(req)

    captured: list[NativeToolCall] = []
    result: dict[str, Any] = {}
    raw: dict[str, Any] | None = None
    exhausted = False
    cancelled = False
    turns_used = 0
    friction = ToolFrictionTracker()
    # When the loop terminates via friction.should_stop, the most recent
    # tool round (assistant tool_use + executed tool_results) was NOT yet
    # appended to ``json_body`` — append_tool_round only runs after the
    # should_stop check. We stash the (raw, tool_results) pair here so the
    # synthesis round (below) can finish appending the round before
    # stripping tools and asking the model for a final summary. When the
    # loop terminates via max_turns (the for-else branch), the last
    # iteration's append_tool_round already ran and this stays None.
    pending_round: tuple[dict[str, Any], list[dict[str, Any]]] | None = None

    persona_token = bind_active_persona(agent)
    try:
        return await _run_native_tool_loop_body(
            model=model,
            req=req,
            send_native=send_native,
            max_turns=max_turns,
            on_tool_event=on_tool_event,
            cancel_check=cancel_check,
            adapter=adapter,
            path=path,
            provider=provider,
            json_body=json_body,
            captured=captured,
            result=result,
            raw=raw,
            exhausted=exhausted,
            cancelled=cancelled,
            turns_used=turns_used,
            friction=friction,
            pending_round=pending_round,
        )
    finally:
        reset_active_persona(persona_token)


async def _run_native_tool_loop_body(
    *,
    model: str,
    req: FrontierRequest,
    send_native: SendNativeFn,
    max_turns: int,
    on_tool_event: ToolEventFn | None,
    cancel_check: CancelCheckFn | None,
    adapter: Any,
    path: str,
    provider: str,
    json_body: dict[str, Any],
    captured: list[NativeToolCall],
    result: dict[str, Any],
    raw: dict[str, Any] | None,
    exhausted: bool,
    cancelled: bool,
    turns_used: int,
    friction: ToolFrictionTracker,
    pending_round: tuple[dict[str, Any], list[dict[str, Any]]] | None,
) -> NativeLoopResult:
    for turn_idx in range(max_turns):
        turns_used = turn_idx + 1

        if cancel_check is not None and cancel_check():
            cancelled = True
            break

        raw = await send_native(path, json_body)
        if not isinstance(raw, dict):
            raise ValueError(
                f"send_native returned non-dict response: {type(raw).__name__}"
            )

        result = adapter.parse_frontier_response(raw)
        tool_calls = result.get("tool_calls")

        if not tool_calls or not req.mcp_tool_loop:
            break

        tool_results, executed = await _execute_tool_calls(
            tool_calls,
            provider,
            turns_used,
            on_tool_event,
            friction=friction,
            max_turns=max_turns,
        )
        captured.extend(executed)
        if friction.should_stop:
            exhausted = True
            pending_round = (raw, tool_results)
            break

        if not hasattr(adapter, "append_tool_round"):
            logger.warning(
                "Adapter %s lacks append_tool_round; stopping tool loop",
                provider,
            )
            break

        adapter.append_tool_round(json_body, raw, tool_results)
    else:
        exhausted = True

    # ------------------------------------------------------------------
    # Synthesis round (provider-history-withholding recovery).
    #
    # When the loop exhausts WITHOUT terminal content, the caller-visible
    # NativeLoopResult.tool_calls still holds every executed round (captured
    # is extended before the break), but the JSON body the provider sees on
    # its NEXT turn is missing the most recent round (friction path) or has
    # no remaining tool turns (max_turns path). Either way, the model
    # never got to summarize.
    #
    # The synthesis round fixes this: append the last round if pending,
    # strip the tool inventory, append an exhaustion advisory, and send one
    # final no-tools request. This turn does NOT count against max_turns
    # (it's outside the for loop). If the adapter doesn't implement the
    # two synthesis hooks, or the synth request itself fails, we fall
    # back to the previous exhausted-with-empty-content behavior.
    # ------------------------------------------------------------------
    synthesized = False
    if (
        exhausted
        and not cancelled
        and hasattr(adapter, "strip_tools")
        and hasattr(adapter, "append_exhaustion_advisory")
    ):
        try:
            if pending_round is not None and hasattr(adapter, "append_tool_round"):
                last_raw, last_results = pending_round
                adapter.append_tool_round(json_body, last_raw, last_results)
            adapter.strip_tools(json_body)
            adapter.append_exhaustion_advisory(json_body, _EXHAUSTION_ADVISORY_TEXT)

            if cancel_check is not None and cancel_check():
                cancelled = True
            else:
                synth_raw = await send_native(path, json_body)
                if isinstance(synth_raw, dict):
                    synth_result = adapter.parse_frontier_response(synth_raw)
                    synth_content = (
                        synth_result.get("content", "")
                        if isinstance(synth_result, dict)
                        else ""
                    )
                    if synth_content:
                        logger.info(
                            "native_loop synthesis round produced %d chars "
                            "after exhaustion (provider=%s, turns_used=%d)",
                            len(synth_content),
                            provider,
                            turns_used,
                        )
                        result = synth_result
                        raw = synth_raw
                        synthesized = True
                    else:
                        logger.info(
                            "native_loop synthesis round returned empty "
                            "content after exhaustion "
                            "(provider=%s, turns_used=%d)",
                            provider,
                            turns_used,
                        )
        except Exception as exc:  # noqa: BLE001 — fall back to prior behavior
            logger.warning(
                "native_loop synthesis round failed "
                "(provider=%s, turns_used=%d): %s. "
                "Falling back to exhausted-empty-content behavior.",
                provider,
                turns_used,
                exc,
            )

    return NativeLoopResult(
        content=result.get("content", "") if isinstance(result, dict) else "",
        # Prefer "reasoning" (populated by ResponsesAPIAdapter for xAI grok-4
        # built-in reasoning) over "thinking". This ensures the field is populated
        # when the adapter fallback triggers.
        reasoning=(
            result.get("reasoning") or result.get("thinking")
            if isinstance(result, dict)
            else None
        ),
        tool_calls=captured,
        turns_used=turns_used,
        exhausted=exhausted,
        cancelled=cancelled,
        usage=(result.get("usage") or {}) if isinstance(result, dict) else {},
        finish_reason=(
            result.get("finish_reason") if isinstance(result, dict) else None
        ),
        block_reason=(result.get("block_reason") if isinstance(result, dict) else None),
        provider=provider,
        raw=raw,
        exhaustion_summary=(
            friction.build_summary(
                execution_id=None,
                turns_used=turns_used,
                tool_calls=captured,
            )
            if exhausted
            else None
        ),
        synthesized=synthesized,
    )

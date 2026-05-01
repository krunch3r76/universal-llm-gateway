"""Provider-native tool-use loop — transport-agnostic.

Runs a bounded multi-turn conversation against a provider-native endpoint
(Anthropic messages, OpenAI/xAI responses, Google generateContent), where
the model can request tool calls that this loop executes locally (via
``libs/agent_seat/executor.execute_tool``) and appends to the conversation
via the adapter's ``append_tool_round``.

Key design: the HTTP transport is injected via ``send_native`` so the same
loop serves both in-process (Stargate pipeline handler) and HTTP-hop (MCP
frontier_generate, tests) callers. The provider-native path string is
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

from agent_seat.executor import execute_tool

logger = logging.getLogger(__name__)


NATIVE_PATHS: dict[str, str] = {
    "anthropic": "/api/v1/providers/anthropic/messages",
    "xai": "/api/v1/providers/xai/responses",
    "openai": "/api/v1/providers/openai/responses",
    "chatgpt": "/api/v1/providers/openai/responses",
    "google": "/api/v1/providers/google/generateContent",
}


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
) -> tuple[list[dict[str, Any]], list[NativeToolCall]]:
    """Execute tool_calls in parallel; return (adapter_tool_results, captured)."""

    async def _run_one(
        raw_call: dict[str, Any],
    ) -> tuple[dict[str, Any], NativeToolCall]:
        name, args, call_id = _normalize_tool_call(raw_call)
        start = time.monotonic()
        ok = True
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
        )
        captured.extend(executed)

        if not hasattr(adapter, "append_tool_round"):
            logger.warning(
                "Adapter %s lacks append_tool_round; stopping tool loop",
                provider,
            )
            break

        adapter.append_tool_round(json_body, raw, tool_results)
    else:
        exhausted = True

    return NativeLoopResult(
        content=result.get("content", "") if isinstance(result, dict) else "",
        # Prefer "reasoning" (populated by ResponsesAPIAdapter for xAI grok-4
        # built-in reasoning) over "thinking". This ensures the field is populated
        # when the adapter fallback triggers.
        reasoning=(
            result.get("reasoning")
            or result.get("thinking")
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
    )

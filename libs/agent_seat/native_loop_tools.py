"""Tool-call normalization and execution for the native tool loop."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

from universal_logging import get_logger

from agent_seat.executor import execute_tool
from agent_seat.native_loop import NativeToolCall, ToolEventFn
from agent_seat.tool_friction import ToolFrictionTracker

logger = get_logger(__name__)


def normalize_tool_call(raw: Any) -> tuple[str, dict[str, Any], str]:
    """Return (name, args, call_id) from provider-native tool-call shapes."""
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


async def execute_tool_calls(
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
        name, args, call_id = normalize_tool_call(raw_call)
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
                        "retry_count": 0,
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

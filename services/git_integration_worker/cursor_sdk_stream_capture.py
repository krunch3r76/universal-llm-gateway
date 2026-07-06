"""Observe a cursor-sdk ``Run.stream()`` for per-tool-call detail.

``run.conversation()`` (the finalized ``GetRunConversation`` RPC) is the
source both ``count_tool_calls`` and ``build_effects_manifest`` read from —
a tool call the runtime truncates or rejects upstream of our MCP server can
be finalized OUT of that conversation while still surfacing on the live
stream as a ``running``/``error`` message. This module iterates
``run.stream()`` before ``run.wait()`` to capture that richer channel:
per-call byte sizes and the SDK's own ``truncated`` field, one event per
call, without changing ``wait()`` semantics (see ``Run.wait`` — a fully
drained stream leaves ``_terminal_result`` cached, so ``wait()`` afterward
is a free cache hit, not a second RPC).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from universal_event_bus import Event, event_factory
from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_events import emit_frontier_event

logger = get_logger(__name__)

_TERMINAL_TOOL_CALL_STATUSES = {"completed", "error"}


@dataclass(frozen=True)
class ToolCallObservation:
    call_id: str
    tool_name: str
    status: str
    arg_bytes: int
    result_bytes: int
    truncated_fields: tuple[str, ...]
    target_path: str | None = None

    @property
    def truncated_any(self) -> bool:
        return bool(self.truncated_fields)


@dataclass(frozen=True)
class StreamCapture:
    tool_calls: tuple[ToolCallObservation, ...]

    @property
    def tool_call_count(self) -> int:
        return len(self.tool_calls)

    @property
    def truncated_tool_calls(self) -> tuple[ToolCallObservation, ...]:
        return tuple(tc for tc in self.tool_calls if tc.truncated_any)


def _json_bytes(value: Any) -> int:
    if value is None:
        return 0
    try:
        return len(json.dumps(value, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        return 0


@event_factory
def FrontierSdkWorkerToolCall(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    resolved_model: str,
    call_id: str,
    tool_name: str,
    status: str,
    arg_bytes: int,
    result_bytes: int,
    truncated: list[str],
    truncated_any: bool,
) -> Event:
    # Per-tool-call detail from run.stream() — a channel distinct from the
    # finalized run.conversation() RPC. A call the runtime truncates/rejects
    # can surface here (running/error) while dropped from the conversation,
    # which is what an aggregate tool_call_count over conversation() can't see.
    return Event(
        signal="frontier.sdk.worker.toolcall",
        payload={
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "resolved_model": resolved_model,
            "call_id": call_id,
            "tool_name": tool_name,
            "status": status,
            "arg_bytes": arg_bytes,
            "result_bytes": result_bytes,
            "truncated": truncated,
            "truncated_any": truncated_any,
        },
        scope="node",
        role="realtime",
    )


_READ_FAMILY_FS_OPS = frozenset({"read", "md_read", "list", "glob", "grep", "search"})


def _string_stream_arg(args: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _target_path_from_stream_args(tool_name: str, args: Any) -> str | None:
    if not isinstance(args, Mapping):
        return None
    name = (tool_name or "").lower()
    if name == "fs" or name.endswith(".fs") or name.endswith("_fs"):
        op = str(args.get("op") or "").lower()
        if op in _READ_FAMILY_FS_OPS:
            return None
        if op and op not in {"write", "append", "edit"}:
            return None
    nested = args.get("args")
    if isinstance(nested, Mapping):
        path = _string_stream_arg(nested, "path", "filePath", "target")
        if path:
            return path
    return _string_stream_arg(args, "path", "filePath", "target")


def _observation_from_message(message: Any) -> ToolCallObservation:
    truncated = getattr(message, "truncated", None) or {}
    args = getattr(message, "args", None)
    tool_name = getattr(message, "name", "") or ""
    return ToolCallObservation(
        call_id=getattr(message, "call_id", "") or "",
        tool_name=tool_name,
        status=str(getattr(message, "status", "")),
        arg_bytes=_json_bytes(args),
        result_bytes=_json_bytes(getattr(message, "result", None)),
        truncated_fields=tuple(sorted(k for k, v in truncated.items() if v)),
        target_path=_target_path_from_stream_args(tool_name, args),
    )


def observe_run_stream(
    run: Any,
    *,
    dispatch_id: str,
    thread_id: str,
    resolved_model: str,
) -> StreamCapture:
    """Drain ``run.stream()``, emitting one ``frontier.sdk.worker.toolcall``
    event per tool call (on terminal status, or flushed at end-of-stream if
    the call never reached one). Never raises — a capture failure degrades
    to a partial/empty result rather than breaking the dispatch.
    """
    latest: dict[str, Any] = {}
    emitted: dict[str, ToolCallObservation] = {}

    def _emit(call_id: str, message: Any) -> None:
        observation = _observation_from_message(message)
        emitted[call_id] = observation
        emit_frontier_event(
            FrontierSdkWorkerToolCall(
                dispatch_id=dispatch_id,
                thread_id=thread_id,
                resolved_model=resolved_model,
                call_id=observation.call_id,
                tool_name=observation.tool_name,
                status=observation.status,
                arg_bytes=observation.arg_bytes,
                result_bytes=observation.result_bytes,
                truncated=list(observation.truncated_fields),
                truncated_any=observation.truncated_any,
            )
        )

    try:
        for message in run.stream():
            if getattr(message, "type", "") != "tool_call":
                continue
            call_id = getattr(message, "call_id", "") or ""
            if not call_id:
                continue
            latest[call_id] = message
            if str(getattr(message, "status", "")) in _TERMINAL_TOOL_CALL_STATUSES:
                _emit(call_id, message)
    except Exception as exc:  # noqa: BLE001 — stream capture must never break the dispatch
        logger.warning(
            "cursor sdk stream capture interrupted: dispatch_id=%s err=%s",
            dispatch_id,
            exc,
        )

    for call_id, message in latest.items():
        if call_id not in emitted:
            _emit(call_id, message)

    return StreamCapture(tool_calls=tuple(emitted[call_id] for call_id in latest))

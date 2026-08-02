"""Observe a cursor-sdk run event stream for per-tool-call + usage detail.

``run.conversation()`` (the finalized ``GetRunConversation`` RPC) is the
source both ``count_tool_calls`` and ``build_effects_manifest`` read from —
a tool call the runtime truncates or rejects upstream of our MCP server can
be finalized OUT of that conversation while still surfacing on the live
stream as a ``running``/``error`` message. This module drains ``run.events()``
(when available) before ``run.wait()``: ``SDKToolUseMessage`` tool calls and
``SDKUsageMessage`` (``type=="usage"``) come from ``RunStreamEvent.sdk_message``;
``TurnEndedUpdate`` / ``TokenDeltaUpdate`` on ``interaction_update`` remain a
secondary path. After ``run.wait()``, ``finalize_stream_capture_usage`` applies
post-wait ``run.usage`` / ``result.usage`` as authority. Falls back to
``run.stream()`` for test doubles.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from universal_event_bus import Event, event_factory
from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_events import emit_frontier_event
from services.git_integration_worker.cursor_sdk_usage_extract import (
    finalize_dispatch_usage,
)
from services.git_integration_worker.cursor_sdk_usage_normalize import (
    TOTAL_DERIVED_KEY,
    UsageCaptureStatus,
    aggregate_stream_usage,
    coerce_non_negative_int,
    normalize_usage_map,
    public_usage,
    usage_payload_from_object,
)

# Re-export for existing callers/tests.
__all__ = [
    "StreamCapture",
    "ToolCallObservation",
    "UsageCaptureStatus",
    "aggregate_stream_usage",
    "finalize_request_id_capture",
    "finalize_stream_capture_usage",
    "normalize_usage_map",
    "observe_run_stream",
    "request_id_from_sdk_error",
]

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
    subagent_type: str | None = None
    args: Mapping[str, Any] | None = None
    result: object | None = None

    @property
    def truncated_any(self) -> bool:
        return bool(self.truncated_fields)


@dataclass(frozen=True)
class StreamCapture:
    tool_calls: tuple[ToolCallObservation, ...]
    usage: dict[str, Any] | None = None
    usage_capture_status: UsageCaptureStatus = "missing"
    # True when stream total was recomputed (not wire) — used only by finalize.
    usage_total_derived: bool = False
    # First stream SDKRequestMessage.request_id (pin: not on RunResult).
    sdk_request_id: str | None = None
    request_id_source: str | None = None

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
    execution_id: str | None = None,
) -> Event:
    # Per-tool-call detail from run.stream() — a channel distinct from the
    # finalized run.conversation() RPC. A call the runtime truncates/rejects
    # can surface here (running/error) while dropped from the conversation,
    # which is what an aggregate tool_call_count over conversation() can't see.
    payload: dict[str, object] = {
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
    }
    if execution_id:
        payload["execution_id"] = execution_id
    return Event(
        signal="frontier.sdk.worker.toolcall",
        payload=payload,
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
    from services.git_integration_worker.cursor_sdk_subagent_capture import (
        subagent_type_from_stream_args,
    )

    return ToolCallObservation(
        call_id=getattr(message, "call_id", "") or "",
        tool_name=tool_name,
        status=str(getattr(message, "status", "")),
        arg_bytes=_json_bytes(args),
        result_bytes=_json_bytes(getattr(message, "result", None)),
        truncated_fields=tuple(sorted(k for k, v in truncated.items() if v)),
        target_path=_target_path_from_stream_args(tool_name, args),
        subagent_type=subagent_type_from_stream_args(tool_name, args),
        args=args if isinstance(args, Mapping) else None,
        result=getattr(message, "result", None),
    )


def _record_usage_message(
    message: Any,
    *,
    turn_usages: list[Mapping[str, Any] | None],
    token_delta_sum: list[int],
) -> None:
    msg_type = getattr(message, "type", "")
    if msg_type == "usage":
        turn_usages.append(usage_payload_from_object(getattr(message, "usage", None)))
        return
    if msg_type == "turn-ended":
        usage = getattr(message, "usage", None)
        if isinstance(usage, Mapping):
            turn_usages.append(usage)
        else:
            turn_usages.append(usage_payload_from_object(usage))
        return
    if msg_type == "token-delta":
        tokens = coerce_non_negative_int(getattr(message, "tokens", None))
        if tokens is not None:
            token_delta_sum[0] += tokens


def _process_tool_call_message(
    message: Any,
    *,
    latest: dict[str, Any],
    emit_fn: Callable[[str, Any], None],
) -> None:
    if getattr(message, "type", "") != "tool_call":
        return
    call_id = getattr(message, "call_id", "") or ""
    if not call_id:
        return
    latest[call_id] = message
    if str(getattr(message, "status", "")) in _TERMINAL_TOOL_CALL_STATUSES:
        emit_fn(call_id, message)


def observe_run_stream(
    run: Any,
    *,
    dispatch_id: str,
    thread_id: str,
    resolved_model: str,
    execution_id: str | None = None,
    on_tool_call: Callable[[ToolCallObservation], None] | None = None,
) -> StreamCapture:
    """Drain run events, emitting one ``frontier.sdk.worker.toolcall`` event
    per tool call (on terminal status, or flushed at end-of-stream if the call
    never reached one). Never raises — a capture failure degrades to a partial/
    empty result rather than breaking the dispatch.

    ``on_tool_call`` (optional, friction 23050) is invoked once per emitted
    observation so callers can maintain a live progress counter (heartbeat)
    without waiting for the drained capture; callback errors are swallowed.
    """
    latest: dict[str, Any] = {}
    emitted: dict[str, ToolCallObservation] = {}
    turn_usages: list[Mapping[str, Any] | None] = []
    token_delta_sum = [0]
    captured_request: list[tuple[str, str]] = []

    def _emit(call_id: str, message: Any) -> None:
        observation = _observation_from_message(message)
        emitted[call_id] = observation
        if on_tool_call is not None:
            try:
                on_tool_call(observation)
            except Exception:  # noqa: BLE001 — telemetry callback must not break capture
                logger.debug(
                    "on_tool_call callback failed: dispatch_id=%s",
                    dispatch_id,
                    exc_info=True,
                )
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
                execution_id=execution_id,
            )
        )

    events_fn = getattr(run, "events", None)
    try:
        if callable(events_fn):
            for event in events_fn():
                interaction = getattr(event, "interaction_update", None)
                if interaction is not None:
                    _record_usage_message(
                        interaction,
                        turn_usages=turn_usages,
                        token_delta_sum=token_delta_sum,
                    )
                sdk_message = getattr(event, "sdk_message", None)
                if sdk_message is not None:
                    if getattr(sdk_message, "type", "") == "request":
                        request_id = getattr(sdk_message, "request_id", None)
                        if request_id and not captured_request:
                            captured_request.append((str(request_id), "stream"))
                    elif getattr(sdk_message, "type", "") == "usage":
                        _record_usage_message(
                            sdk_message,
                            turn_usages=turn_usages,
                            token_delta_sum=token_delta_sum,
                        )
                    else:
                        _process_tool_call_message(
                            sdk_message, latest=latest, emit_fn=_emit
                        )
        else:
            for message in run.stream():
                _record_usage_message(
                    message, turn_usages=turn_usages, token_delta_sum=token_delta_sum
                )
                _process_tool_call_message(message, latest=latest, emit_fn=_emit)
    except Exception as exc:  # noqa: BLE001 — stream capture must never break the dispatch
        logger.warning(
            "cursor sdk stream capture interrupted: dispatch_id=%s err=%s",
            dispatch_id,
            exc,
        )

    for call_id, message in latest.items():
        if call_id not in emitted:
            _emit(call_id, message)

    usage, usage_capture_status = aggregate_stream_usage(
        turn_usages=tuple(turn_usages),
        token_delta_sum=token_delta_sum[0],
    )
    derived = bool(usage and usage.get(TOTAL_DERIVED_KEY))
    sdk_request_id = captured_request[0][0] if captured_request else None
    request_id_source = captured_request[0][1] if captured_request else None
    return StreamCapture(
        tool_calls=tuple(emitted[call_id] for call_id in latest),
        usage=public_usage(usage),
        usage_capture_status=usage_capture_status,
        usage_total_derived=derived,
        sdk_request_id=sdk_request_id,
        request_id_source=request_id_source,
    )


def finalize_request_id_capture(
    capture: StreamCapture,
    *,
    run: Any = None,
    result: Any = None,
) -> StreamCapture:
    """Apply post-wait Run/RunResult request_id when stream capture missed it."""
    if capture.sdk_request_id:
        return capture
    request_id = None
    if result is not None:
        request_id = getattr(result, "request_id", None)
    if not request_id and run is not None:
        request_id = getattr(run, "request_id", None)
    if not request_id:
        return capture
    return StreamCapture(
        tool_calls=capture.tool_calls,
        usage=capture.usage,
        usage_capture_status=capture.usage_capture_status,
        usage_total_derived=capture.usage_total_derived,
        sdk_request_id=str(request_id),
        request_id_source="post_wait",
    )


def finalize_stream_capture_usage(
    capture: StreamCapture,
    *,
    run: Any = None,
    result: Any = None,
) -> StreamCapture:
    """Apply post-wait ``run.usage`` / ``result.usage`` as authority over stream."""
    record = finalize_dispatch_usage(capture, run=run, result=result)
    return StreamCapture(
        tool_calls=capture.tool_calls,
        usage=record.usage,
        usage_capture_status=record.usage_capture_status,
        usage_total_derived=False,
        sdk_request_id=capture.sdk_request_id,
        request_id_source=capture.request_id_source,
    )


def request_id_from_sdk_error(exc: BaseException) -> tuple[str | None, str | None]:
    """Fallback request_id from CursorSDKError when stream capture missed it."""
    request_id = getattr(exc, "request_id", None)
    if request_id:
        return str(request_id), "error"
    return None, None

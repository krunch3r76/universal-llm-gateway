"""Observe a cursor-sdk run event stream for per-tool-call + usage detail.

``run.conversation()`` (the finalized ``GetRunConversation`` RPC) is the
source both ``count_tool_calls`` and ``build_effects_manifest`` read from —
a tool call the runtime truncates or rejects upstream of our MCP server can
be finalized OUT of that conversation while still surfacing on the live
stream as a ``running``/``error`` message. This module drains ``run.events()``
(when available) before ``run.wait()``: ``SDKToolUseMessage`` tool calls come
from ``RunStreamEvent.sdk_message``; ``TurnEndedUpdate`` / ``TokenDeltaUpdate``
usage meters live on ``RunStreamEvent.interaction_update`` and are **not**
yielded by ``run.stream()``. Falls back to ``run.stream()`` for test doubles.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from universal_event_bus import Event, event_factory
from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_events import emit_frontier_event

UsageCaptureStatus = Literal["captured", "partial", "missing"]

_INPUT_TOKEN_KEYS = ("input_tokens", "prompt_tokens", "input", "inputTokens")
_OUTPUT_TOKEN_KEYS = ("output_tokens", "completion_tokens", "output", "outputTokens")
_TOTAL_TOKEN_KEYS = ("total_tokens", "total", "totalTokens")
_SPEND_PASS_THROUGH_KEYS = ("cost_usd", "credits", "spend", "cost")

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
    usage: dict[str, Any] | None = None
    usage_capture_status: UsageCaptureStatus = "missing"

    @property
    def tool_call_count(self) -> int:
        return len(self.tool_calls)

    @property
    def truncated_tool_calls(self) -> tuple[ToolCallObservation, ...]:
        return tuple(tc for tc in self.tool_calls if tc.truncated_any)


def _coerce_non_negative_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 else None
    if isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return None
        return parsed if parsed >= 0 else None
    return None


def _first_token_count(raw: Mapping[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        if key in raw:
            parsed = _coerce_non_negative_int(raw[key])
            if parsed is not None:
                return parsed
    return None


def normalize_usage_map(raw: Mapping[str, Any]) -> tuple[dict[str, Any] | None, bool]:
    """Map SDK ``TurnEndedUpdate.usage`` to input/output/total (+ optional spend).

    Returns ``(normalized, mappable)``. When keys are opaque, ``mappable`` is
    False and callers should persist ``usage_raw`` with ``partial`` status.
    """
    input_tokens = _first_token_count(raw, _INPUT_TOKEN_KEYS)
    output_tokens = _first_token_count(raw, _OUTPUT_TOKEN_KEYS)
    total_tokens = _first_token_count(raw, _TOTAL_TOKEN_KEYS)
    if input_tokens is None and output_tokens is None and total_tokens is None:
        return None, False
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    normalized: dict[str, Any] = {}
    if input_tokens is not None:
        normalized["input_tokens"] = input_tokens
    if output_tokens is not None:
        normalized["output_tokens"] = output_tokens
    if total_tokens is not None:
        normalized["total_tokens"] = total_tokens
    for key in _SPEND_PASS_THROUGH_KEYS:
        if key in raw:
            normalized[key] = raw[key]
    return normalized, True


def _sum_normalized_usages(items: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    """F5: sum per-turn mappable usage maps before emit."""
    aggregated: dict[str, Any] = {}
    for field in ("input_tokens", "output_tokens", "total_tokens"):
        values = [item[field] for item in items if field in item]
        if values:
            aggregated[field] = sum(values)
    for key in _SPEND_PASS_THROUGH_KEYS:
        for item in reversed(items):
            if key in item:
                aggregated[key] = item[key]
                break
    return aggregated


def aggregate_stream_usage(
    *,
    turn_usages: tuple[Mapping[str, Any] | None, ...],
    token_delta_sum: int,
) -> tuple[dict[str, Any] | None, UsageCaptureStatus]:
    """Derive terminal usage + ``usage_capture_status`` from stream observations.

    ``partial`` when ≥1 turn has usage and ≥1 does not (R F-A5), when only
    ``TokenDeltaUpdate`` totals exist (F1 fallthrough), or when keys are opaque.
    ``captured`` requires every turn-ended message carried mappable usage (F5 sum).
    ``missing`` when no turn usage and no token deltas were observed.
    """
    turns_with_usage = sum(1 for usage in turn_usages if usage)
    turns_without_usage = sum(1 for usage in turn_usages if not usage)
    mixed_turns = turns_with_usage > 0 and turns_without_usage > 0

    normalized_turns: list[dict[str, Any]] = []
    for raw in turn_usages:
        if not raw:
            continue
        normalized, mappable = normalize_usage_map(raw)
        if not mappable:
            return {"usage_raw": dict(raw)}, "partial"
        if normalized is not None:
            normalized_turns.append(normalized)

    if normalized_turns:
        aggregated = _sum_normalized_usages(tuple(normalized_turns))
        if mixed_turns:
            return aggregated, "partial"
        if turn_usages and turns_without_usage == 0:
            return aggregated, "captured"
        return aggregated, "captured"

    if token_delta_sum > 0:
        return {"total_tokens": token_delta_sum}, "partial"

    return None, "missing"


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


def _record_usage_message(
    message: Any,
    *,
    turn_usages: list[Mapping[str, Any] | None],
    token_delta_sum: list[int],
) -> None:
    msg_type = getattr(message, "type", "")
    if msg_type == "turn-ended":
        usage = getattr(message, "usage", None)
        turn_usages.append(usage if isinstance(usage, Mapping) else None)
        return
    if msg_type == "token-delta":
        tokens = _coerce_non_negative_int(getattr(message, "tokens", None))
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
    return StreamCapture(
        tool_calls=tuple(emitted[call_id] for call_id in latest),
        usage=usage,
        usage_capture_status=usage_capture_status,
    )

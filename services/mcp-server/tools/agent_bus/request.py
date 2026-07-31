"""agent_bus ``request`` — life-callable Cursor Auto admit channel.

Writes a send-equivalent turn (injecting ``lane:cursor-auto``), probes a live
Auto handler, enqueues when armed, and returns ``{thread, turn, handler_status,
poll_hint}``. Distinct from ``send`` and from ``lane:life-to-code``.

Sender wire discipline (harvest-restart-propagation I3): enqueue JSON fields added
by MCP must remain optional-with-default — never rename or remove existing keys.
"""

from __future__ import annotations

from typing import Any

from mcp_events import record

from .._agent_bus_author import resolve_dispatch_from_agent
from .request_intake import (
    resolve_contract_intake,
    resolve_request_id_intake,
    stamp_contract_deprecation,
)
from .request_worker_client import enqueue_auto_job, probe_auto_liveness
from .send import _send_dispatch

_LANE_TAG = "lane:cursor-auto"
_SUGGESTED_INTERVAL_S = 2
_MAX_EXPECTED_LATENCY_S = 120


def _merge_lane_tags(tags: list[str] | None) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for t in list(tags or []) + [_LANE_TAG]:
        if t not in seen:
            merged.append(t)
            seen.add(t)
    return merged


def _build_poll_hint(*, thread_id: str, after_turn: int) -> dict[str, Any]:
    return {
        "tool": "wait",
        "arguments_json": {
            "thread": str(thread_id),
            "after_turn": after_turn,
            "completion": "status:done",
            "wait_seconds": 0,
        },
        "suggested_interval_s": _SUGGESTED_INTERVAL_S,
        "max_expected_latency_s": _MAX_EXPECTED_LATENCY_S,
        "alternate_completions": [
            "status:failed",
            "status:needs-attended",
            "status:blocked",
        ],
    }


def _annotate_poll_hint_no_producer(poll_hint: dict[str, Any]) -> dict[str, Any]:
    return {**poll_hint, "producer": "none"}


def _build_enqueue_failure(
    *,
    reason: str,
    attempts: int,
    error_class: str,
    elapsed_s: float,
) -> dict[str, Any]:
    return {
        "reason": reason,
        "attempts": max(0, int(attempts)),
        "error_class": error_class,
        "elapsed_s": max(0.0, float(elapsed_s)),
        "terminal_park": True,
    }


def _error_class_from_liveness(liveness: dict[str, Any]) -> str:
    if liveness.get("error_class"):
        return str(liveness["error_class"])
    reason = str(liveness.get("reason", ""))
    if reason == "no_live_handler":
        return "handler_dead"
    if reason == "liveness_http_error":
        status = liveness.get("status_code")
        if isinstance(status, int) and 500 <= status < 600:
            return "http_5xx"
        return "http_other"
    return "unknown"


def _enqueue_failure_reason(enq: dict[str, Any]) -> str:
    if enq.get("reason"):
        return str(enq["reason"])
    enqueue_data = enq.get("enqueue") or {}
    if enqueue_data.get("handler_status"):
        return str(enqueue_data["handler_status"])
    return str(enq.get("handler_status", "no-auto-handler"))


def _error_class_from_enqueue(enq: dict[str, Any]) -> str:
    reason = str(enq.get("reason") or "")
    if reason == "enqueue_unreachable":
        return "enqueue_unreachable"
    enqueue_data = enq.get("enqueue") or {}
    worker_status = str(
        enqueue_data.get("handler_status") or enq.get("handler_status") or ""
    )
    if worker_status in {"no_live_auto_handler", "no-auto-handler"}:
        return "handler_dead"
    status = enq.get("status_code")
    if isinstance(status, int) and 500 <= status < 600:
        return "http_5xx"
    if isinstance(status, int):
        return "http_other"
    return "unknown"


def _request_impl(
    *,
    new_slug: str | None,
    thread: str | None,
    to: str,
    subject: str,
    body: str,
    from_agent: str,
    tags: list[str] | None,
    sidecar_content: str | None,
    sidecar_slug: str | None,
    desired_model: str,
    desired_effort: str,
    contract: str,
    require_attended: bool,
    request_id: str | None,
    after_turn: int,
    summary: str | None = None,
) -> dict[str, Any]:
    """Write turn via send path, then arm/enqueue Auto when live."""
    from pager_notify.so_what import resolve_so_what_summary

    from .lifecycle import _update_thread_impl

    merged_tags = _merge_lane_tags(tags)
    resolved_summary = resolve_so_what_summary(summary, body)
    send_result = _send_dispatch(
        new_slug=new_slug,
        thread=thread,
        to=to,
        subject=subject,
        body=body,
        from_agent=from_agent,
        summary=resolved_summary,
        tags=merged_tags,
        after_turn=after_turn,
        sidecar_content=sidecar_content,
        sidecar_slug=sidecar_slug,
    )
    if isinstance(send_result, dict) and "error" in send_result:
        record("mcp.agentbus.request.failed", error=str(send_result.get("error")))
        return send_result

    thread_obj = send_result.get("thread") or {}
    turn_obj = send_result.get("turn") or {}
    thread_id = str(thread_obj.get("id") or thread or "")
    turn_number = int(turn_obj.get("turn_number") or 1)
    # Continue-path send does not PATCH summary; mint already applied it.
    if resolved_summary and thread_id and not new_slug:
        patched = _update_thread_impl(
            thread=thread_id,
            status=None,
            summary=resolved_summary,
            tags=None,
            from_agent=from_agent,
        )
        if isinstance(patched, dict) and "error" not in patched and patched.get("id"):
            thread_obj = patched
        else:
            thread_obj = {**dict(thread_obj), "summary": resolved_summary}
    elif resolved_summary and isinstance(thread_obj, dict):
        thread_obj = {**dict(thread_obj), "summary": resolved_summary}
    # Hoist send-path sidecar fields — callers (esp. Cowork) check top-level
    # sidecar_uri; dropping them made successful writes look like failures
    # (a:26439 item 5 / todo:agent-bus-sidecar-uri-null-on-write).
    sidecar_uri = send_result.get("sidecar_uri")
    sidecar_sha256 = send_result.get("sidecar_sha256")
    if sidecar_uri is None and isinstance(turn_obj, dict):
        sidecar_uri = turn_obj.get("sidecar_uri")
    if sidecar_sha256 is None and isinstance(turn_obj, dict):
        sidecar_sha256 = turn_obj.get("sidecar_sha256")

    liveness = probe_auto_liveness()
    if not liveness.get("live"):
        reason = str(liveness.get("reason", "no_live_handler"))
        attempts = int(liveness.get("attempts", 1))
        elapsed_s = float(liveness.get("elapsed_s", 0.0))
        error_class = _error_class_from_liveness(liveness)
        record(
            "mcp.agentbus.request.degraded",
            thread=thread_id,
            turn_number=turn_number,
            reason=reason,
            error_class=error_class,
            elapsed_s=elapsed_s,
            attempts=attempts,
        )
        degraded = {
            "thread": thread_obj,
            "turn": turn_obj,
            "handler_status": "no-auto-handler",
            "poll_hint": _annotate_poll_hint_no_producer(
                _build_poll_hint(thread_id=thread_id, after_turn=turn_number)
            ),
            "liveness": liveness,
            "enqueue_failure": _build_enqueue_failure(
                reason=reason,
                attempts=attempts,
                error_class=error_class,
                elapsed_s=elapsed_s,
            ),
            "tags": merged_tags,
            "sidecar_uri": sidecar_uri,
            "sidecar_sha256": sidecar_sha256,
        }
        if request_id:
            degraded["request_id"] = request_id
        return degraded

    enq = enqueue_auto_job(
        thread_id=thread_id,
        turn_number=turn_number,
        subject=subject,
        body=body,
        from_agent=from_agent,
        to_agent=to,
        desired_model=desired_model,
        desired_effort=desired_effort,
        contract=contract,
        require_attended=require_attended,
        request_id=request_id,
    )
    if not enq.get("ok"):
        reason = _enqueue_failure_reason(enq)
        attempts = int(liveness.get("attempts", 1))
        elapsed_s = float(liveness.get("elapsed_s", 0.0))
        error_class = _error_class_from_enqueue(enq)
        record(
            "mcp.agentbus.request.degraded",
            thread=thread_id,
            turn_number=turn_number,
            reason=reason,
            error_class=error_class,
            elapsed_s=elapsed_s,
            attempts=attempts,
        )
        result = {
            "thread": thread_obj,
            "turn": turn_obj,
            "handler_status": "no-auto-handler",
            "poll_hint": _annotate_poll_hint_no_producer(
                _build_poll_hint(thread_id=thread_id, after_turn=turn_number)
            ),
            "enqueue": enq,
            "enqueue_failure": _build_enqueue_failure(
                reason=reason,
                attempts=attempts,
                error_class=error_class,
                elapsed_s=elapsed_s,
            ),
            "liveness": liveness,
            "tags": merged_tags,
            "sidecar_uri": sidecar_uri,
            "sidecar_sha256": sidecar_sha256,
        }
        if request_id:
            result["request_id"] = request_id
        return result

    handler_status = "auto-admit-armed"
    record(
        "mcp.agentbus.request.posted",
        thread=thread_id,
        turn_number=turn_number,
        handler_status=handler_status,
        desired_model=desired_model,
        contract=contract,
    )
    result = {
        "thread": thread_obj,
        "turn": turn_obj,
        "handler_status": handler_status,
        "poll_hint": _build_poll_hint(thread_id=thread_id, after_turn=turn_number),
        "enqueue": enq,
        "tags": merged_tags,
        "sidecar_uri": sidecar_uri,
        "sidecar_sha256": sidecar_sha256,
    }
    if request_id:
        result["request_id"] = request_id
    return result


def _request_dispatch(
    *,
    new_slug: str | None = None,
    thread: str | int | None = None,
    to: str = "cursor",
    subject: str = "",
    body: str = "",
    from_agent: str = "",
    tags: list[str] | None = None,
    sidecar_content: str | None = None,
    sidecar_slug: str | None = None,
    desired_model: str = "auto",
    desired_effort: str = "medium",
    contract: str = "answer",
    require_attended: bool = False,
    request_id: str | None = None,
    after_turn: int = 0,
    summary: str | None = None,
) -> dict[str, Any]:
    """Validate + dispatch ``agent_bus.request``.

    ``require_attended`` (default false): when true, Auto refuses unattended
    nested dispatch and in-seat substitute — terminal ``status:needs-attended``
    with ``reason=operator_require_attended``. Body field ``require_attended:
    true`` or ``executor_bind: attended`` ORs with the wire param.

    ``summary``: standing human so-what title (ULG outcome line). Also accepted
    fail-soft via body ``so_what:`` / ``ulg_gain:`` when wire summary omitted.

    ``request_id``: optional caller idempotency key; echoed on success; duplicate
    values are refused (``duplicate_request_id``) before the turn is written.

    ``contract``: one of ``answer|confer|investigate|implement|verify|execute|propagate``.
    Unknown values are rejected (422 ``request_contract_unknown``) before the
    turn is written; legacy ``consult`` is aliased to ``confer`` with a
    deprecation note on the response. ``execute`` = one tier-M allowlisted op;
    ``propagate`` = operator restart request (propagation ledger + drain-gated
    sync_restart — not tier-M ``manage.*``).
    """
    if isinstance(thread, int):
        thread = str(thread)

    from_agent, author_err = resolve_dispatch_from_agent(from_agent)
    if author_err is not None:
        return author_err

    has_new_slug = new_slug is not None
    has_thread = bool(thread)
    if has_new_slug == has_thread:
        record("mcp.agentbus.request.rejected", reason="xor")
        return {
            "error": (
                "request: exactly one of thread or new_slug is required"
            ),
            "reason": "request_xor_violation",
        }
    if not subject or not body:
        return {
            "error": "request: subject and body are required",
            "missing_fields": [
                f for f, v in (("subject", subject), ("body", body)) if not v
            ],
        }
    if to and to != "cursor":
        return {
            "error": "request: v0 only supports to='cursor'",
            "reason": "request_to_unsupported",
            "provided": to,
        }

    intake = resolve_contract_intake(contract, from_agent=from_agent)
    if intake.error is not None:
        return intake.error

    thread_hint = str(thread) if thread is not None else None
    rid_intake = resolve_request_id_intake(
        request_id,
        thread_id=thread_hint,
        contract=intake.contract,
        from_agent=from_agent,
    )
    if rid_intake.error is not None:
        return rid_intake.error

    result = _request_impl(
        new_slug=new_slug,
        thread=thread,
        to=to or "cursor",
        subject=subject,
        body=body,
        from_agent=from_agent,
        tags=tags,
        sidecar_content=sidecar_content,
        sidecar_slug=sidecar_slug,
        desired_model=desired_model or "auto",
        desired_effort=desired_effort or "medium",
        contract=intake.contract,
        require_attended=bool(require_attended),
        request_id=rid_intake.request_id,
        after_turn=after_turn,
        summary=summary,
    )
    return stamp_contract_deprecation(result, intake)

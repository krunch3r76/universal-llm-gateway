"""agent_bus ``request`` — retired cursor-auto entry (a:38728).

``_request_dispatch`` returns ``cursor_auto_retired_refusal`` and does not
write a turn. ``_request_impl`` stays until slice 2. ``_resolve_hop_seat_request_refusal``
stays; hop imports it. ``resolve_request_id_intake`` lives in ``request_intake``
and hop imports it from there.
"""

from __future__ import annotations

from typing import Any

from agent_bus_store.disposition import append_bus_lifecycle_tags
from mcp_events import record

from .park_hint import build_poll_hint as _build_poll_hint
from .park_hint import is_chat_delivery_capable
from .request_cse_bind import maybe_bind_thread_cse
from .send import _send_dispatch

_CURSOR_AUTO_RETIRED_ERROR = (
    "cursor-auto is retired (a:38728). Code commission: team_dispatch on ulg-code. "
    "Life CSE: life_dispatch. A bus turn: agent_bus send."
)


def cursor_auto_retired_refusal() -> dict[str, str]:
    """Teaching refusal for the retired cursor-auto commission path."""
    record("mcp.agentbus.request.rejected", reason="cursor_auto_retired")
    return {
        "error": _CURSOR_AUTO_RETIRED_ERROR,
        "reason": "cursor_auto_retired",
    }


def _resolve_hop_seat_request_refusal(
    *,
    thread_id: str | None,
    cse_registration_id: str | None,
    from_agent: str | None = None,
    audit: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Bind identity and refuse superseded predecessor writes when fenced.

    Refuses ``ambiguous_matches``; ``zero_matches`` / ``empty_snap`` only when
    the watch holds a ``registration_id`` (registration-less enroll stub admits on N=0).
    """
    from claude_bundles.request_admission_identity import gate_request_admission

    refusal = gate_request_admission(
        thread_id=thread_id,
        caller_registration_id=cse_registration_id,
        from_agent=from_agent,
        audit=audit,
    )
    if refusal is None:
        return None
    data = refusal.get("data") if isinstance(refusal.get("data"), dict) else {}
    record(
        "mcp.agentbus.request.rejected",
        reason=str(data.get("reason") or "hop_seat_refusal"),
        thread=thread_id,
        registration_id=cse_registration_id,
        code=str(refusal.get("code") or ""),
    )
    return refusal


def _side_effect_failure(op: str, exc: BaseException) -> dict[str, str]:
    """Advisory row for a post-enqueue side effect that must not fail the call."""
    return {
        "op": op,
        "error_type": type(exc).__name__,
        "error": str(exc),
    }


def _merge_lane_tags(tags: list[str] | None) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    stamped = append_bus_lifecycle_tags(list(tags or []), bus_lifecycle="persistent")
    for t in stamped:
        if t not in seen:
            merged.append(t)
            seen.add(t)
    return merged


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
    cse_chat_url: str | None = None,
    cse_registration_id: str | None = None,
    escalation: str | None = None,
    continuity_hop: bool = False,
    lane: str | None = None,
    workspace: str | None = None,
    parent_thread: str | None = None,
    lane_role: str | None = None,
    prompt_uri: str | None = None,
    advisor_brief: str | None = None,
    census_mismatch: bool = False,
    work_key: str | None = None,
    allow_long_body: bool = False,
    enqueue_body: str | None = None,
) -> dict[str, Any]:
    """Write turn via send path. The Auto enqueue arm is gone."""
    from pager_notify.so_what import resolve_so_what_summary

    from .lifecycle import _update_thread_impl

    merged_tags = _merge_lane_tags(tags)
    if census_mismatch and "census_mismatch" not in merged_tags:
        merged_tags.append("census_mismatch")
    thread_tags_for_summary: list[str] | None = list(merged_tags) if new_slug else None
    if thread and not new_slug:
        # Host agent-bus owns messages.db. MCP in the container relays;
        # a local get_thread opens /data/messages.db and fails the hop.
        from ._shared import relay

        detail = relay("agent-bus", "GET", f"/threads/{thread}/summary?recent=1")
        if (
            isinstance(detail, dict)
            and "error" not in detail
            and isinstance(detail.get("tags"), list)
        ):
            thread_tags_for_summary = list(detail["tags"])
    resolved_summary = resolve_so_what_summary(
        summary,
        body,
        from_agent=from_agent,
        thread_tags=thread_tags_for_summary,
    )
    # Mission / operator-proxy private lanes must enter A′ candidacy at birth.
    # NULL bus_lifecycle_state means unenrolled; with-turn birth → active
    # (legal None→active). Do not use pending — that path expects dispatch-admit.
    send_result = _send_dispatch(
        new_slug=new_slug,
        thread=thread,
        to=to,
        subject=subject,
        body=body,
        from_agent=from_agent,
        summary=resolved_summary,
        tags=merged_tags,
        lifecycle_state="active" if new_slug is not None else None,
        after_turn=after_turn,
        sidecar_content=sidecar_content,
        sidecar_slug=sidecar_slug,
        allow_long_body=allow_long_body,
        parent_thread=parent_thread,
        lane_role=lane_role,
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

    maybe_bind_thread_cse(
        thread_id=thread_id,
        from_agent=from_agent,
        cse_chat_url=cse_chat_url,
        cse_registration_id=cse_registration_id,
        continuity_hop=continuity_hop,
    )

    capture_identity = is_chat_delivery_capable(from_agent) or continuity_hop
    side_effect_failures: list[dict[str, str]] = []
    if (
        capture_identity
        and (cse_chat_url or cse_registration_id)
        and not continuity_hop
    ):
        from claude_bundles.cse_session_obligations import stamp_session_ids

        try:
            stamp_session_ids(
                lane_thread=str(thread_id),
                chat_url=cse_chat_url,
                registration_id=cse_registration_id,
            )
        except Exception as exc:  # noqa: BLE001 — post-write advisory, not the receipt
            side_effect_failures.append(_side_effect_failure("stamp_session_ids", exc))
        from .cse_provenance_enrich import enrich_request_provenance

        try:
            enrich_request_provenance(
                lane_thread=str(thread_id),
                chat_url=cse_chat_url,
                registration_id=cse_registration_id,
            )
        except Exception as exc:  # noqa: BLE001 — post-write advisory, not the receipt
            side_effect_failures.append(
                _side_effect_failure("enrich_request_provenance", exc)
            )
    record(
        "mcp.agentbus.request.posted",
        thread=thread_id,
        turn_number=turn_number,
        contract=contract,
    )
    result = {
        "thread": thread_obj,
        "turn": turn_obj,
        "poll_hint": _build_poll_hint(
            thread_id=thread_id,
            after_turn=turn_number,
            from_agent=from_agent,
        ),
        "tags": merged_tags,
        "sidecar_uri": sidecar_uri,
        "sidecar_sha256": sidecar_sha256,
    }
    if request_id:
        result["request_id"] = request_id
    if lane:
        result["lane"] = lane
    if side_effect_failures:
        result["side_effect_failures"] = side_effect_failures
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
    desired_effort: str = "auto",
    contract: str = "answer",
    require_attended: bool = False,
    request_id: str | None = None,
    after_turn: int = 0,
    summary: str | None = None,
    cse_chat_url: str | None = None,
    cse_registration_id: str | None = None,
    escalation: str | None = None,
    lane: str | None = None,
    workspace: str | None = None,
    parent_thread: str | None = None,
    lane_role: str | None = None,
    prompt_uri: str | None = None,
    advisor_brief: str | None = None,
    work_key: str | None = None,
) -> dict[str, Any]:
    """Retired cursor-auto entry (a:38728). Refuses before any turn write."""
    return cursor_auto_retired_refusal()

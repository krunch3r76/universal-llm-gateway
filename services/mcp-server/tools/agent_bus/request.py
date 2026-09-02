"""agent_bus ``request`` — life-callable Cursor Auto admit channel.

Writes a send-equivalent turn (injecting ``lane:cursor-auto``), probes a live
Auto handler, enqueues when armed, and returns ``{thread, turn, handler_status,
poll_hint}``. Distinct from ``send`` and from ``lane:life-to-code``.

Sender wire discipline (harvest-restart-propagation I3): enqueue JSON fields added
by MCP must remain optional-with-default — never rename or remove existing keys.
"""

from __future__ import annotations

from typing import Any

from agent_bus_store.disposition import append_bus_lifecycle_tags
from mcp_events import record

from .._agent_bus_author import resolve_dispatch_from_agent
from .lane_provenance import observe_unparented_birth
from .park_hint import build_poll_hint as _build_poll_hint
from .park_hint import is_chat_delivery_capable
from .request_cse_bind import maybe_bind_thread_cse
from .request_failure import (
    annotate_poll_hint_no_producer,
    build_enqueue_failure,
    enqueue_failure_reason,
    error_class_from_enqueue,
    error_class_from_liveness,
)
from .request_intake import (
    resolve_checkout_lane,
    resolve_contract_intake,
    resolve_request_id_intake,
    stamp_contract_deprecation,
)
from .request_worker_client import enqueue_auto_job, probe_auto_liveness
from .send import _send_dispatch

_LANE_TAG = "lane:cursor-auto"


def _resolve_hop_seat_request_refusal(
    *,
    thread_id: str | None,
    cse_registration_id: str | None,
    from_agent: str | None = None,
) -> dict[str, Any] | None:
    """Bind identity and refuse superseded predecessor writes when fenced.

    Also returns ``seat.identity_unresolvable`` when census N≠1 on the
    default path (``ambiguous_matches`` / ``zero_matches`` / ``empty_snap``).
    """
    from claude_bundles.request_admission_identity import gate_request_admission

    refusal = gate_request_admission(
        thread_id=thread_id,
        caller_registration_id=cse_registration_id,
        from_agent=from_agent,
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


def _merge_lane_tags(tags: list[str] | None) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    stamped = append_bus_lifecycle_tags(list(tags or []), bus_lifecycle="persistent")
    for t in stamped + [_LANE_TAG]:
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
) -> dict[str, Any]:
    """Write turn via send path, then arm/enqueue Auto when live."""
    from pager_notify.so_what import resolve_so_what_summary

    from .lifecycle import _update_thread_impl

    merged_tags = _merge_lane_tags(tags)
    resolved_summary = resolve_so_what_summary(summary, body)
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
    )

    liveness = probe_auto_liveness()
    if not liveness.get("live"):
        reason = str(liveness.get("reason", "no_live_handler"))
        attempts = int(liveness.get("attempts", 1))
        elapsed_s = float(liveness.get("elapsed_s", 0.0))
        error_class = error_class_from_liveness(liveness)
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
            "poll_hint": annotate_poll_hint_no_producer(
                _build_poll_hint(
                    thread_id=thread_id,
                    after_turn=turn_number,
                    from_agent=from_agent,
                )
            ),
            "liveness": liveness,
            "enqueue_failure": build_enqueue_failure(
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

    capture_identity = is_chat_delivery_capable(from_agent)
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
        cse_chat_url=cse_chat_url if capture_identity else None,
        cse_registration_id=cse_registration_id if capture_identity else None,
        escalation=escalation,
        continuity_hop=continuity_hop,
        lane=lane,
        workspace=workspace,
        prompt_uri=prompt_uri,
        advisor_brief=advisor_brief,
    )
    if not enq.get("ok"):
        reason = enqueue_failure_reason(enq)
        attempts = int(liveness.get("attempts", 1))
        elapsed_s = float(liveness.get("elapsed_s", 0.0))
        error_class = error_class_from_enqueue(enq)
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
            "poll_hint": annotate_poll_hint_no_producer(
                _build_poll_hint(
                    thread_id=thread_id,
                    after_turn=turn_number,
                    from_agent=from_agent,
                )
            ),
            "enqueue": enq,
            "enqueue_failure": build_enqueue_failure(
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
    posted_kw: dict[str, Any] = {
        "thread": thread_id,
        "turn_number": turn_number,
        "handler_status": handler_status,
        "desired_model": desired_model,
        "contract": contract,
    }
    if lane:
        posted_kw["lane"] = lane
    if request_id:
        posted_kw["request_id"] = request_id
    record("mcp.agentbus.request.posted", **posted_kw)
    if capture_identity and (cse_chat_url or cse_registration_id) and not continuity_hop:
        from claude_bundles.cse_session_obligations import stamp_session_ids

        stamp_session_ids(
            lane_thread=str(thread_id),
            chat_url=cse_chat_url,
            registration_id=cse_registration_id,
        )
        from .cse_provenance_enrich import enrich_request_provenance

        enrich_request_provenance(
            lane_thread=str(thread_id),
            chat_url=cse_chat_url,
            registration_id=cse_registration_id,
        )
    # Promote lane discriminant out of the double-nested HTTP body so a
    # caller of agent_bus.request need not dig to enqueue.enqueue.* —
    # same keys remain beside superseded on the worker body for parity.
    enqueue_body = enq.get("enqueue") if isinstance(enq.get("enqueue"), dict) else {}
    lane_pending = enqueue_body.get("same_thread_pending")
    lane_claimed = enqueue_body.get("same_thread_claimed")
    if lane_pending is not None or lane_claimed is not None:
        enq = dict(enq)
        if lane_pending is not None:
            enq["same_thread_pending"] = lane_pending
        if lane_claimed is not None:
            enq["same_thread_claimed"] = lane_claimed
    result = {
        "thread": thread_obj,
        "turn": turn_obj,
        "handler_status": handler_status,
        "poll_hint": _build_poll_hint(
            thread_id=thread_id,
            after_turn=turn_number,
            from_agent=from_agent,
        ),
        "enqueue": enq,
        "tags": merged_tags,
        "sidecar_uri": sidecar_uri,
        "sidecar_sha256": sidecar_sha256,
    }
    if lane_pending is not None:
        result["same_thread_pending"] = lane_pending
    if lane_claimed is not None:
        result["same_thread_claimed"] = lane_claimed
    if request_id:
        result["request_id"] = request_id
    if lane:
        result["lane"] = lane
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

    ``contract``: one of ``answer|confer|ask|investigate|implement|verify|execute|propagate|seed|recon``.
    Unknown values are rejected (422 ``request_contract_unknown``) before the
    turn is written; legacy ``consult`` is aliased to ``confer`` with a
    deprecation note on the response. ``execute`` = one tier-M allowlisted op;
    ``propagate`` = operator restart request (propagation ledger + drain-gated
    sync_restart — not tier-M ``manage.*``).

    ``lane``: optional GIW checkout-isolation ``A`` (local master) or ``B``
    (``cursor-sdk/lane-{thread}``). Omit for current ``select_lane`` defaults.
    ``parent_thread`` + ``lane_role`` may atomically bind a newly minted
    bus-thread lane; both must be supplied together. Distinct from tag
    ``lane:cursor-auto``. Invalid values reject 422 ``request_lane_invalid``
    before the turn is written.

    ``prompt_uri`` / ``advisor_brief``: sealed advisor brief for CDP escalation.
    GIW ``AutoJob`` already stores these; omitting them on this surface ships
    ``job.body`` instead (``prompt_source=job.body``).
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
            "error": ("request: exactly one of thread or new_slug is required"),
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

    seat_refusal = _resolve_hop_seat_request_refusal(
        thread_id=thread_hint,
        cse_registration_id=cse_registration_id,
        from_agent=from_agent,
    )
    if seat_refusal is not None:
        return seat_refusal

    checkout_lane, lane_err = resolve_checkout_lane(lane, from_agent=from_agent)
    if lane_err is not None:
        return lane_err
    observe_unparented_birth(
        new_slug=new_slug,
        parent_thread=parent_thread,
        lane_role=lane_role,
        request_id=rid_intake.request_id,
    )

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
        desired_effort=desired_effort or "auto",
        contract=intake.contract,
        require_attended=bool(require_attended),
        request_id=rid_intake.request_id,
        after_turn=after_turn,
        summary=summary,
        cse_chat_url=cse_chat_url,
        cse_registration_id=cse_registration_id,
        escalation=escalation,
        lane=checkout_lane,
        workspace=workspace,
        parent_thread=parent_thread,
        lane_role=lane_role,
        prompt_uri=prompt_uri,
        advisor_brief=advisor_brief,
    )
    return stamp_contract_deprecation(result, intake)

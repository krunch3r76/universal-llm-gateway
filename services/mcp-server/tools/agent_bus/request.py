"""agent_bus ``request`` — retired cursor-auto entry (a:38728).

``_request_dispatch`` returns ``cursor_auto_retired_refusal`` before author
resolution, validation, CSE bind, or any turn write. ``_request_impl``,
``request_intake``, ``request_cse_bind``, and ``request_worker_client`` stay
until slice 2 deletes them. ``_resolve_hop_seat_request_refusal`` stays; hop
imports it.
"""

from __future__ import annotations

from typing import Any

from agent_bus_store.disposition import append_bus_lifecycle_tags
from mcp_events import record

from .._agent_bus_author import resolve_dispatch_from_agent
from .lane_associations import refuse_lane_bind_incomplete_pair
from .lane_provenance import observe_unparented_birth
from .park_hint import build_poll_hint as _build_poll_hint
from .park_hint import is_chat_delivery_capable
from .request_cse_bind import maybe_bind_thread_cse
from .request_intake import (
    resolve_checkout_lane,
    resolve_contract_intake,
    resolve_request_id_intake,
    stamp_contract_deprecation,
)
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

    ``lane``: optional GIW checkout-isolation. In-repo checkout uses lane B
    (pass ``B``). Omit is not that default: empty ``files_expected`` + omit
    selects Lane A (``select_lane`` ``opt_out``). ``parent_thread`` +
    ``lane_role`` may atomically bind a newly minted
    bus-thread lane; both must be supplied together. Invalid values reject 422 ``request_lane_invalid``
    before the turn is written.

    ``prompt_uri`` / ``advisor_brief``: sealed advisor brief for CDP escalation.
    GIW ``AutoJob`` already stores these; omitting them on this surface ships
    ``job.body`` instead (``prompt_source=job.body``).

    ``work_key``: optional D4 identity. With ``lane=B`` and contract
    ``investigate|recon|verify|implement|conductor`` plus a scheme-prefixed
    key (``todo:``, ``plan:``, ``plan_phase:``, ``packet:``, ``agent-bus:``,
    ``friction:``, ``decision:``) GIW selects concurrent Auto admission.
    Omit the key, or use lane A, and the job stays serial. Same thread still
    supersedes. ``conductor`` is recognized by that predicate; bus intake
    still rejects it as ``request_contract_unknown`` — probes use ``implement``.

    Slice 1 (a:38728): this entry refuses before any of the work below.
    """
    return cursor_auto_retired_refusal()
    if isinstance(thread, int):  # pragma: no cover
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

    admission_audit: dict[str, Any] = {}
    seat_refusal = _resolve_hop_seat_request_refusal(
        thread_id=thread_hint,
        cse_registration_id=cse_registration_id,
        from_agent=from_agent,
        audit=admission_audit,
    )
    if seat_refusal is not None:
        return seat_refusal

    checkout_lane, lane_err = resolve_checkout_lane(lane, from_agent=from_agent)
    if lane_err is not None:
        return lane_err
    if work_key is not None and str(work_key).strip():
        from .._frontier_intake import validate_work_key

        work_key_err = validate_work_key(str(work_key).strip())
        if work_key_err is not None:
            return work_key_err
        work_key = str(work_key).strip()
    lane_bind_refusal = refuse_lane_bind_incomplete_pair(
        parent_thread=parent_thread,
        lane_role=lane_role,
    )
    if lane_bind_refusal is not None:
        return lane_bind_refusal
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
        census_mismatch=bool(admission_audit.get("census_mismatch")),
        work_key=work_key,
    )
    if (
        admission_audit.get("census_mismatch")
        and isinstance(result, dict)
        and "error" not in result
    ):
        result["census_mismatch"] = True
    return stamp_contract_deprecation(result, intake)

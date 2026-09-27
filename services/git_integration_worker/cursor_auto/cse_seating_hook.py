"""CSE seating hook — occupy holder row on continuity hop (R-7 G5 path)."""

from __future__ import annotations

from typing import Any

from hop_handoff import parse_occupy_target, parse_superseded_registration_id
from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.hop_cadence_home_lane import (
    watch_thread_for_job,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob

logger = get_logger(__name__)

__all__ = [
    "on_successor_seated",
    "record_seated_registration",
    "retire_predecessors_on_seat",
    "run_cse_seating_hook",
]


def _resolve_successor_identity(
    job: AutoJob,
    execution_id: str,
) -> tuple[str | None, str | None]:
    """Best-effort registration/chat_url for the commissioned successor."""
    lane = watch_thread_for_job(job)
    exec_id = (execution_id or "").strip()
    if not lane or not exec_id:
        return None, None
    try:
        from claude_bundles.cdp_registry.session_address import (
            chat_url_for_registration,
        )

        from services.git_integration_worker.cursor_auto.cdp_escalation import (
            read_cdp_lane_snapshot,
        )
        from services.git_integration_worker.cursor_auto.hop_cadence_predecessor import (
            op_row_for_execution_on_lane,
        )

        snap = read_cdp_lane_snapshot()
        if not isinstance(snap, dict):
            return None, None
        aw_row = op_row_for_execution_on_lane(snap, lane, exec_id)
        if aw_row is None:
            return None, None
        reg = str(aw_row.get("registration_id") or "").strip() or None
        chat = str(aw_row.get("chat_url") or "").strip() or None
        if not chat and reg:
            chat = (chat_url_for_registration(reg) or "").strip() or None
        return reg, chat
    except Exception as exc:  # noqa: BLE001 — hook must not crash hop
        logger.warning(
            "cse_seating_hook identity resolve failed job=%s: %s",
            job.job_id,
            exc,
        )
        return None, None


def run_cse_seating_hook(
    job: AutoJob,
    *,
    execution_id: str,
) -> dict[str, Any]:
    """Upsert the occupy-target holder row and supersede predecessor on hop.

    Returns a payload with ``path`` naming which branch ran. Emits
    ``giw.cursor_auto.cse_seating_hook`` for path-scoped regression tests.
    """
    lane = watch_thread_for_job(job)
    occupy = parse_occupy_target(job.body) or (job.cse_chat_url or "").strip() or None
    superseded = (
        parse_superseded_registration_id(job.body)
        or (job.cse_registration_id or "").strip()
        or None
    )
    if not occupy:
        outcome: dict[str, Any] = {
            "ok": False,
            "path": "skipped_no_occupy_target",
            "reason": "no_occupy_target",
            "thread_id": str(job.thread_id),
        }
        _emit_hook(outcome)
        return outcome

    new_reg, new_chat = _resolve_successor_identity(job, execution_id)
    from claude_bundles.cse_url import normalize_cse_url

    seated_chat = (new_chat or "").strip()
    occupy_norm = normalize_cse_url(occupy) if occupy else ""
    seated_norm = normalize_cse_url(seated_chat) if seated_chat else ""
    # Identity follows the seated row. When that URL is not occupy_target,
    # do not stamp the successor registration onto the occupy holder.
    reg_for_occupy = new_reg
    if new_reg and seated_norm and occupy_norm and seated_norm != occupy_norm:
        reg_for_occupy = None
    from services.git_integration_worker.cse_session_holders import (
        ensure_schema,
        occupy_holder_on_hop,
    )
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    with CursorDispatchLedger.instance()._connect() as conn:
        ensure_schema(conn)
        outcome = occupy_holder_on_hop(
            conn,
            occupy_target=occupy,
            lane_thread_id=lane,
            superseded_registration_id=superseded,
            new_registration_id=reg_for_occupy,
            new_execution_id=(execution_id or "").strip() or None,
        )
        if new_reg and seated_chat:
            on_successor_seated(
                {"rows": [], "seated_rows": []},
                parent_thread=str(lane or job.thread_id),
                registration_id=new_reg,
                chat_url=seated_chat,
                execution_id=(execution_id or "").strip() or None,
                occupy_target=occupy,
                conn=conn,
            )
        conn.commit()
    outcome["thread_id"] = str(job.thread_id)
    outcome["occupy_target"] = occupy
    outcome["superseded_registration_id"] = superseded
    _emit_hook(outcome)
    return outcome


def record_seated_registration(
    *,
    chat_url: str | None,
    registration_id: str | None,
    execution_id: str | None = None,
) -> dict[str, Any] | None:
    """Write a registration confirm actually observed onto an existing holder.

    Arm-time seating leaves ``registration_id`` null when the successor
    execution is not in the snapshot yet. The hop wire id is admission
    identity, not proof of who sat down.
    """
    url = (chat_url or "").strip()
    reg = (registration_id or "").strip()
    if not url or not reg:
        return None
    from claude_bundles.cse_url import normalize_cse_url
    from claude_bundles.holder_strings import holder_id_from_chat_url

    from services.git_integration_worker.cse_session_holders import (
        ensure_schema,
        get_holder,
        upsert_holder,
    )
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    hid = holder_id_from_chat_url(normalize_cse_url(url))
    if not hid:
        return None
    with CursorDispatchLedger.instance()._connect() as conn:
        ensure_schema(conn)
        if get_holder(conn, hid) is None:
            return None
        row = upsert_holder(
            conn,
            chat_url=url,
            registration_id=reg,
            execution_id=(execution_id or "").strip() or None,
        )
        conn.commit()
    return row or None


def _retire_identity_row(row: dict[str, Any]) -> dict[str, Any]:
    retired = dict(row)
    retired["stream_state"] = "none"
    retired["seat_state"] = "superseded"
    retired["status"] = "superseded"
    return retired


def retire_predecessors_on_seat(
    snap: dict[str, Any],
    *,
    parent_thread: str,
    successor_registration_id: str,
    chat_url: str | None = None,
    execution_id: str | None = None,
) -> dict[str, Any]:
    """Retire every other live identity row for ``parent_thread`` in this snap.

    Read-time census is unchanged: hop plus driving stays N=2 until this
    write. After it, ``census_match_ids`` returns only the successor.
    """
    from claude_bundles.request_admission_census import census_match_ids

    tid = (parent_thread or "").strip()
    successor = (successor_registration_id or "").strip()
    out = dict(snap)
    before = census_match_ids(tid, out) if tid and successor else []
    found = False
    for key in ("rows", "seated_rows", "seat_rows"):
        raw = out.get(key)
        if not isinstance(raw, list):
            continue
        nxt: list[Any] = []
        for row in raw:
            if not isinstance(row, dict):
                nxt.append(row)
                continue
            reg = str(row.get("registration_id") or "").strip()
            parent = str(row.get("parent_thread") or "").strip()
            if parent == tid and reg and reg != successor:
                nxt.append(_retire_identity_row(row))
                continue
            if parent == tid and reg == successor:
                found = True
            nxt.append(row)
        out[key] = nxt
    if successor and tid and not found:
        seated = list(out.get("seated_rows") or [])
        seated.append(
            {
                "registration_id": successor,
                "execution_id": (execution_id or "").strip()
                or "__none:seated_no_stream__",
                "parent_thread": tid,
                "purpose": "operator-proxy",
                "seat_state": "active",
                "stream_state": "running" if (execution_id or "").strip() else "none",
                "chat_url": (chat_url or "").strip() or None,
                "source": "cse-session-registry",
            }
        )
        out["seated_rows"] = seated
    prior = [
        str(item).strip()
        for item in (out.get("retired_registration_ids") or [])
        if str(item).strip() and str(item).strip() != successor
    ]
    retired_ids = [
        item for item in dict.fromkeys([*prior, *before]) if item != successor
    ]
    out["retired_registration_ids"] = retired_ids
    return out


def on_successor_seated(
    snap: dict[str, Any],
    *,
    parent_thread: str,
    registration_id: str,
    chat_url: str,
    execution_id: str | None = None,
    occupy_target: str | None = None,
    conn: Any | None = None,
    watches: dict[str, Any] | None = None,
    thread_row: dict[str, Any] | None = None,
    bind_registry: bool = False,
    bind_thread: bool = False,
) -> dict[str, Any]:
    """Seat ``registration_id`` as the only live census row for the lane.

    Holder, watch, and thread identity are copied from this seated row.
    ``occupy_target`` is accepted and ignored so a hop target cannot
    overwrite the seated registration.
    """
    _ = occupy_target
    parent = (parent_thread or "").strip()
    reg = (registration_id or "").strip()
    url = (chat_url or "").strip()
    seated = retire_predecessors_on_seat(
        snap,
        parent_thread=parent,
        successor_registration_id=reg,
        chat_url=url or None,
        execution_id=execution_id,
    )
    if conn is not None and url and reg:
        from services.git_integration_worker.cse_session_holders import upsert_holder

        upsert_holder(
            conn,
            chat_url=url,
            registration_id=reg,
            execution_id=(execution_id or "").strip() or None,
            lane_thread_id=parent or None,
        )
    if watches is not None and parent and reg:
        row = dict(watches.get(parent) or {"thread_id": parent})
        row["thread_id"] = parent
        row["registration_id"] = reg
        row["chat_url"] = url or row.get("chat_url")
        row["retired_registration_ids"] = list(
            seated.get("retired_registration_ids") or []
        )
        watches[parent] = row
    if thread_row is not None and reg:
        thread_row["cse_registration_id"] = reg
        if url:
            thread_row["cse_chat_url"] = url
    if bind_registry and reg:
        try:
            from claude_bundles.cdp_registry.session_address import (
                retire_predecessor_identity,
            )

            retire_predecessor_identity(reg, parent_thread=parent or None)
        except Exception as exc:  # noqa: BLE001 — seat write must not crash hop
            logger.warning(
                "retire_predecessor_identity failed reg=%s: %s", reg, exc
            )
    if bind_thread and parent and reg and url:
        try:
            from agent_bus_store.db.cse_associations import associate_cse

            associate_cse(
                thread_id=parent,
                cse_chat_url=url,
                cse_registration_id=reg,
                bound_by="cse_seating_hook",
                evidence="cdp.generate.seated",
            )
        except Exception as exc:  # noqa: BLE001 — missing thread is not a hop failure
            logger.warning(
                "seated thread bind failed thread=%s: %s", parent, exc
            )
    return seated


def _emit_hook(payload: dict[str, Any]) -> None:
    from services.git_integration_worker.events import publish_lib_signal

    try:
        publish_lib_signal("giw.cursor_auto.cse_seating_hook", payload)
    except Exception as exc:  # noqa: BLE001 — observability must not block hop
        logger.debug("cse_seating_hook signal failed: %s", exc)

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
    "seat_successor_from_dispatch_link",
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
        return _seat_successor_without_occupy(
            job,
            lane=str(lane or job.thread_id),
            execution_id=(execution_id or "").strip(),
        )

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
            outcome["successor_seated"] = True
        else:
            outcome["successor_seated"] = False
        conn.commit()
    outcome["thread_id"] = str(job.thread_id)
    outcome["occupy_target"] = occupy
    outcome["superseded_registration_id"] = superseded
    _emit_hook(outcome)
    return outcome


def _load_identity_snap() -> dict[str, Any]:
    """Active-work snap plus registry seated rows, for arm-time census."""
    snap: dict[str, Any] = {}
    try:
        from services.git_integration_worker.cursor_auto.cdp_escalation import (
            read_cdp_lane_snapshot,
        )

        loaded = read_cdp_lane_snapshot()
        if isinstance(loaded, dict):
            snap = loaded
    except Exception as exc:  # noqa: BLE001 — seating must not crash the hop
        logger.warning("cse_seating_hook snap load failed: %s", exc)
        snap = {}
    try:
        from claude_bundles.hop_cadence_seat_snap import attach_registry_seated_rows

        return attach_registry_seated_rows(snap)
    except Exception as exc:  # noqa: BLE001 — registry read is best-effort
        logger.warning("cse_seating_hook registry attach failed: %s", exc)
        return snap


def _watch_targets() -> list[Any]:
    """Admission and cadence watch files. One path when they already match."""
    from claude_bundles.hop_seat_cutover import watches_path as admission_watches_path

    from services.git_integration_worker.cursor_auto.hop_cadence_watch import (
        watches_path as cadence_watches_path,
    )

    targets = []
    for path in (admission_watches_path(), cadence_watches_path()):
        if path not in targets:
            targets.append(path)
    return targets


def _chat_url_from_dispatch_link(execution_id: str) -> str | None:
    """CSE URL persisted on the generate's dispatch link, if the file exists.

    Arm-time snapshots often lack the window. The link row is written when
    the CDP generate binds ``chat_url`` (turn 383's execution on 12286).
    """
    exec_id = (execution_id or "").strip()
    if not exec_id:
        return None
    try:
        import os

        path = os.environ.get("AGENT_BUS_DB_PATH", "/data/messages.db")
        if not path or not os.path.isfile(path):
            return None
        import sqlite3

        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
        try:
            row = conn.execute(
                "SELECT chat_url FROM thread_dispatch_links "
                "WHERE execution_id=? AND chat_url IS NOT NULL AND chat_url != '' "
                "ORDER BY chat_url_bound_at DESC LIMIT 1",
                (exec_id,),
            ).fetchone()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — seating must not crash the hop
        logger.warning(
            "cse_seating_hook dispatch-link chat_url failed exec=%s: %s",
            exec_id,
            exc,
        )
        return None
    if row is None:
        return None
    url = str(row[0] or "").strip()
    return url or None


def _live_holder_registration_ids(
    conn: Any,
    *,
    lane: str,
    successor: str,
) -> list[str]:
    """Registration ids of driving/dormant holders on *lane* other than *successor*."""
    rows = conn.execute(
        "SELECT registration_id FROM cse_session_holders "
        "WHERE lane_thread_id=? AND seat_state IN ('driving', 'dormant')",
        (lane,),
    ).fetchall()
    found: list[str] = []
    for row in rows:
        reg = str(row["registration_id"] or "").strip()
        if reg and reg != successor and reg not in found:
            found.append(reg)
    return found


def _persist_successor_watch(
    *,
    lane: str,
    successor: str,
    execution_id: str,
    successor_birth_id: str | None,
    retired_registration_ids: list[str],
    chat_url: str | None = None,
) -> None:
    """Point the lane watch at the hop successor and drop stale census ids."""
    from services.git_integration_worker.cursor_auto.hop_cadence_watch import (
        load_watches,
        save_watches,
    )

    updates: dict[str, Any] = {
        "thread_id": lane,
        "registration_id": successor,
        "execution_id": execution_id,
        "retired_registration_ids": list(retired_registration_ids),
    }
    url = (chat_url or "").strip()
    if url:
        updates["chat_url"] = url
    birth = (successor_birth_id or "").strip()
    if birth:
        updates["successor_birth_id"] = birth
    for path in _watch_targets():
        watches = load_watches(path)
        row = dict(watches.get(lane) or {"thread_id": lane})
        row.update(updates)
        watches[lane] = row
        save_watches(watches, path)


def _seat_successor_without_occupy(
    job: AutoJob,
    *,
    lane: str,
    execution_id: str,
) -> dict[str, Any]:
    """Seat a Cowork/CDP successor that has no occupy_target.

    The census key is ``successor_birth_id`` until a real registration is
    resolved for ``execution_id``. Predecessor census ids and same-lane
    holder registration ids are written onto the watch. The successor
    window URL comes from the identity snap or the generate dispatch link.
    Holder rows for the lane move only when that URL is known: successor
    driving, predecessors superseded, registry peers closed. With no URL
    the census watch still keys the birth id, and both the holder table
    and the registry stay put. The deferred flip is
    ``seat_successor_from_dispatch_link`` on a later reconcile tick, when
    ``thread_dispatch_links`` has a chat_url for this execution. Closing
    the registry first would leave the predecessor holder as the wake target.
    """
    from hop_handoff import parse_successor_birth_id

    birth = (parse_successor_birth_id(job.body) or "").strip()
    resolved_reg, resolved_chat = _resolve_successor_identity(job, execution_id)
    successor = (resolved_reg or birth).strip()
    if not successor or not execution_id:
        outcome = {
            "ok": False,
            "path": "skipped_no_occupy_target",
            "reason": "no_successor_key" if not successor else "no_execution_id",
            "thread_id": str(job.thread_id),
            "successor_seated": False,
        }
        _emit_hook(outcome)
        return outcome

    chat = (resolved_chat or "").strip() or (
        _chat_url_from_dispatch_link(execution_id) or ""
    )
    snap = _load_identity_snap()
    holder_regs: list[str] = []
    superseded_holders: list[str] = []
    holders_seated = False
    from services.git_integration_worker.cse_session_holders import (
        ensure_schema,
        seat_successor_on_lane,
    )
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    with CursorDispatchLedger.instance()._connect() as conn:
        ensure_schema(conn)
        holder_regs = _live_holder_registration_ids(
            conn, lane=lane, successor=successor
        )
        if chat:
            seated_holders = seat_successor_on_lane(
                conn,
                chat_url=chat,
                registration_id=successor,
                lane_thread_id=lane,
                execution_id=execution_id,
            )
            if not seated_holders.get("ok"):
                outcome = {
                    "ok": False,
                    "path": "refused_live_peer",
                    "reason": str(
                        seated_holders.get("reason")
                        or "occupy_target_held_by_live_peer"
                    ),
                    "thread_id": str(job.thread_id),
                    "lane_thread_id": lane,
                    "successor_registration_id": successor,
                    "successor_chat_url": chat,
                    "held_by_lane": seated_holders.get("held_by_lane"),
                    "successor_seated": False,
                }
                _emit_hook(outcome)
                return outcome
            superseded_holders = [
                str(item)
                for item in (seated_holders.get("superseded_holder_ids") or [])
                if str(item).strip()
            ]
            holders_seated = True
        seated = on_successor_seated(
            snap,
            parent_thread=lane,
            registration_id=successor,
            chat_url=chat,
            execution_id=execution_id,
            bind_registry=holders_seated,
            bind_thread=False,
        )
        conn.commit()
    retired = [
        str(item).strip()
        for item in (seated.get("retired_registration_ids") or [])
        if str(item).strip() and str(item).strip() != successor
    ]
    if holders_seated:
        for reg in holder_regs:
            if reg not in retired:
                retired.append(reg)
    _persist_successor_watch(
        lane=lane,
        successor=successor,
        execution_id=execution_id,
        successor_birth_id=birth or None,
        retired_registration_ids=retired,
        chat_url=chat or None,
    )
    outcome = {
        "ok": True,
        "path": "seated_without_occupy_target",
        "reason": "successor_keyed_by_birth_and_execution",
        "thread_id": str(job.thread_id),
        "lane_thread_id": lane,
        "successor_registration_id": successor,
        "successor_chat_url": chat or None,
        "execution_id": execution_id,
        "successor_birth_id": birth or None,
        "successor_seated": True,
        "holders_seated": holders_seated,
        "retired_registration_ids": retired,
        "superseded_holder_ids": superseded_holders,
    }
    _emit_hook(outcome)
    return outcome


def seat_successor_from_dispatch_link(
    *,
    lane_thread_id: str,
    execution_id: str,
    successor_birth_id: str,
) -> dict[str, Any]:
    """Drive the successor from the hop execution's dispatch-link chat_url.

    Hub-local trigger: ``thread_dispatch_links.chat_url`` for *execution_id*.
    The registration key is *successor_birth_id*. One call supersedes the
    lane's other driving holders and appends the thread-CSE association.
    No chat_url means no write — a window that never opened stays on the
    predecessor. The holder commit happens before the thread append; a
    thread-bind failure leaves the holder flipped so a later tick can
    retry the association only.
    """
    lane = (lane_thread_id or "").strip()
    exec_id = (execution_id or "").strip()
    birth = (successor_birth_id or "").strip()
    if not lane or not exec_id or not birth:
        return {
            "ok": False,
            "path": "dispatch_link",
            "reason": "missing_lane_execution_or_birth",
            "holders_seated": False,
            "thread_bound": False,
            "superseded_holder_ids": [],
        }
    chat = _chat_url_from_dispatch_link(exec_id) or ""
    if not chat:
        return {
            "ok": False,
            "path": "dispatch_link",
            "reason": "no_dispatch_link",
            "lane_thread_id": lane,
            "execution_id": exec_id,
            "successor_birth_id": birth,
            "holders_seated": False,
            "thread_bound": False,
            "superseded_holder_ids": [],
        }
    from agent_bus_store.db.cse_associations import (
        associate_cse,
        normalize_cse_bind_url,
    )

    if normalize_cse_bind_url(chat) is None:
        return {
            "ok": False,
            "path": "dispatch_link",
            "reason": "chat_url_not_bindable",
            "lane_thread_id": lane,
            "execution_id": exec_id,
            "successor_chat_url": chat,
            "holders_seated": False,
            "thread_bound": False,
            "superseded_holder_ids": [],
        }
    from services.git_integration_worker.cse_session_holders import (
        ensure_schema,
        seat_successor_on_lane,
    )
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    with CursorDispatchLedger.instance()._connect() as conn:
        ensure_schema(conn)
        retired_regs = _live_holder_registration_ids(conn, lane=lane, successor=birth)
        seated = seat_successor_on_lane(
            conn,
            chat_url=chat,
            registration_id=birth,
            lane_thread_id=lane,
            execution_id=exec_id,
        )
        if not seated.get("ok"):
            return {
                "ok": False,
                "path": "dispatch_link",
                "reason": str(
                    seated.get("reason") or "occupy_target_held_by_live_peer"
                ),
                "lane_thread_id": lane,
                "execution_id": exec_id,
                "successor_birth_id": birth,
                "successor_chat_url": chat,
                "held_by_lane": seated.get("held_by_lane"),
                "holders_seated": False,
                "thread_bound": False,
                "superseded_holder_ids": [],
            }
        conn.commit()
    superseded = [
        str(item)
        for item in (seated.get("superseded_holder_ids") or [])
        if str(item).strip()
    ]
    try:
        associate_cse(
            thread_id=lane,
            cse_chat_url=chat,
            cse_registration_id=birth,
            bound_by="cse_seating_hook",
            evidence="thread_dispatch_links.chat_url",
        )
    except Exception as exc:  # noqa: BLE001 — holder flip must survive a bind miss
        logger.warning(
            "dispatch-link thread bind failed lane=%s exec=%s: %s",
            lane,
            exec_id,
            exc,
        )
        outcome = {
            "ok": False,
            "path": "dispatch_link",
            "reason": "thread_bind_failed",
            "lane_thread_id": lane,
            "execution_id": exec_id,
            "successor_birth_id": birth,
            "successor_registration_id": birth,
            "successor_chat_url": chat,
            "holders_seated": True,
            "thread_bound": False,
            "superseded_holder_ids": superseded,
            "retired_registration_ids": retired_regs,
        }
        _emit_hook(outcome)
        return outcome
    outcome = {
        "ok": True,
        "path": "dispatch_link",
        "reason": "successor_seated_from_dispatch_link",
        "lane_thread_id": lane,
        "execution_id": exec_id,
        "successor_birth_id": birth,
        "successor_registration_id": birth,
        "successor_chat_url": chat,
        "holders_seated": True,
        "thread_bound": True,
        "superseded_holder_ids": superseded,
        "retired_registration_ids": retired_regs,
    }
    _emit_hook(outcome)
    return outcome


def record_seated_registration(
    *,
    chat_url: str | None,
    registration_id: str | None,
    execution_id: str | None = None,
    lane_thread_id: str | None = None,
) -> dict[str, Any] | None:
    """Write a registration confirm actually observed onto an existing holder.

    Arm-time seating leaves ``registration_id`` null when the successor
    execution is not in the snapshot yet. The hop wire id is admission
    identity, not proof of who sat down. When ``lane_thread_id`` is set
    this is the late window bind: the holder becomes the lane's driving
    row and registry peers on that lane close with it.
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
    lane = (lane_thread_id or "").strip()
    holder: dict[str, Any] | None
    with CursorDispatchLedger.instance()._connect() as conn:
        ensure_schema(conn)
        if lane:
            from services.git_integration_worker.cse_session_holders import (
                seat_successor_on_lane,
            )

            seated = seat_successor_on_lane(
                conn,
                chat_url=url,
                registration_id=reg,
                lane_thread_id=lane,
                execution_id=(execution_id or "").strip() or None,
            )
            if not seated.get("ok"):
                return None
            conn.commit()
            seated_holder = seated.get("holder")
            holder = seated_holder if isinstance(seated_holder, dict) else None
        else:
            if get_holder(conn, hid) is None:
                return None
            row = upsert_holder(
                conn,
                chat_url=url,
                registration_id=reg,
                execution_id=(execution_id or "").strip() or None,
            )
            conn.commit()
            holder = row or None
    if lane and holder is not None:
        try:
            from claude_bundles.cdp_registry.session_address import (
                retire_predecessor_identity,
            )

            retire_predecessor_identity(reg, parent_thread=lane)
        except Exception as exc:  # noqa: BLE001 — confirm must not crash the hop
            logger.warning(
                "retire_predecessor_identity failed reg=%s lane=%s: %s",
                reg,
                lane,
                exc,
            )
    return holder


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
            logger.warning("retire_predecessor_identity failed reg=%s: %s", reg, exc)
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
            logger.warning("seated thread bind failed thread=%s: %s", parent, exc)
    return seated


def _emit_hook(payload: dict[str, Any]) -> None:
    from services.git_integration_worker.events import publish_lib_signal

    try:
        publish_lib_signal("giw.cursor_auto.cse_seating_hook", payload)
    except Exception as exc:  # noqa: BLE001 — observability must not block hop
        logger.debug("cse_seating_hook signal failed: %s", exc)

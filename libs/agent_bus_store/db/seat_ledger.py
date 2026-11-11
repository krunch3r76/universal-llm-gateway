"""Append-only lane seat ledger (Phase 2 shadow). ``transition_seat`` is the sole writer."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Literal

from claude_bundles.cdp_reclaim_refuse import cse_id_from_url
from claude_bundles.cse_url import normalize_cse_url
from universal_logging import get_logger

from ..db.cse_associations import HOP_SEATED_BOUND_BY
from .connection import connect, now, write_connect
from .threads import get_thread, normalize_thread_id

logger = get_logger(__name__)

_TRANSITIONS = frozenset({"birth", "hop", "rebind", "close", "backfill"})
_STATES = frozenset({"open", "closed", "contested"})
_TRUTHY = frozenset({"1", "true", "yes", "on"})

_shadow_hook_error_count = 0
_contested_wake_quarantine_count = 0


def _reset_seat_ledger_counters_for_tests() -> None:
    global _shadow_hook_error_count, _contested_wake_quarantine_count
    _shadow_hook_error_count = 0
    _contested_wake_quarantine_count = 0


def seat_ledger_shadow_writes_enabled() -> bool:
    """When false, shadow hooks are no-ops (explicit ``AGENT_BUS_SEAT_LEDGER_SHADOW=0``)."""
    raw = os.environ.get("AGENT_BUS_SEAT_LEDGER_SHADOW", "1")
    return str(raw).strip().lower() in _TRUTHY


def shadow_hook_error_count() -> int:
    return _shadow_hook_error_count


def contested_wake_quarantine_count() -> int:
    return _contested_wake_quarantine_count


def _record_shadow_hook_error(*, where: str, thread_id: str) -> None:
    global _shadow_hook_error_count
    _shadow_hook_error_count += 1
    logger.warning(
        "seat_ledger shadow hook failed (fail-open) where=%s thread_id=%s",
        where,
        thread_id,
        exc_info=True,
    )


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def _normalize_chat_url(raw: str | None) -> str | None:
    url = normalize_cse_url(raw or "")
    return url or None


def _chat_id_for_url(url: str | None) -> str | None:
    return cse_id_from_url(url or "")


@dataclass
class SeatLedgerError(Exception):
    code: str
    message: str
    body: dict[str, Any]

    def __str__(self) -> str:
        return self.message


def _latest_row(conn: sqlite3.Connection, *, thread_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM thread_seat_ledger "
        "WHERE thread_id = ? ORDER BY epoch DESC LIMIT 1",
        (thread_id,),
    ).fetchone()


def _holder_of_chat(conn: sqlite3.Connection, *, chat_id: str) -> sqlite3.Row | None:
    if not chat_id:
        return None
    return conn.execute(
        """
        SELECT tsl.*
        FROM thread_seat_ledger tsl
        INNER JOIN (
            SELECT thread_id, MAX(epoch) AS max_epoch
            FROM thread_seat_ledger
            GROUP BY thread_id
        ) cur ON tsl.thread_id = cur.thread_id AND tsl.epoch = cur.max_epoch
        WHERE tsl.chat_id = ? AND tsl.state = 'open'
        LIMIT 1
        """,
        (chat_id,),
    ).fetchone()


def _by_request_id(conn: sqlite3.Connection, request_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM thread_seat_ledger WHERE request_id = ?",
        (request_id,),
    ).fetchone()


def transition_seat(
    *,
    thread_id: str,
    transition: Literal["birth", "hop", "rebind", "close", "backfill"],
    chat_url: str | None = None,
    expected_epoch: int | None = None,
    actor: str,
    evidence: str | dict[str, Any] | None = None,
    request_id: str,
    contested: bool = False,
) -> dict[str, Any]:
    """Append one seat ledger row. Sole insert path for ``thread_seat_ledger``."""
    if transition not in _TRANSITIONS:
        raise SeatLedgerError(
            code="seat_transition_invalid",
            message=f"Invalid transition {transition!r}",
            body={"transition": transition},
        )
    thread_id = normalize_thread_id(thread_id)
    if get_thread(thread_id) is None:
        raise LookupError(f"Thread {thread_id} not found")

    evidence_json: str | None
    if evidence is None:
        evidence_json = None
    elif isinstance(evidence, str):
        evidence_json = evidence
    else:
        evidence_json = json.dumps(evidence, sort_keys=True)

    request_id = (request_id or "").strip()
    if not request_id:
        raise SeatLedgerError(
            code="seat_transition_invalid",
            message="request_id is required",
            body={},
        )

    with write_connect() as conn:
        prior = _by_request_id(conn, request_id)
        if prior is not None:
            row = _row_to_dict(prior)
            assert row is not None
            row["idempotent_replay"] = True
            return row

        current = _latest_row(conn, thread_id=thread_id)
        current_epoch = int(current["epoch"]) if current is not None else 0
        current_state = str(current["state"]) if current is not None else "closed"

        if transition == "birth":
            if current is not None and current_state != "closed":
                raise SeatLedgerError(
                    code="seat_transition_invalid",
                    message="birth requires closed or empty lane",
                    body={"current": _row_to_dict(current)},
                )
            if contested:
                state = "contested"
                norm_url = None
                chat_id = None
            else:
                state = "open"
                norm_url = _normalize_chat_url(chat_url)
                if norm_url is None:
                    raise SeatLedgerError(
                        code="seat_transition_invalid",
                        message="birth requires chat_url unless contested",
                        body={},
                    )
                chat_id = _chat_id_for_url(norm_url)
            next_epoch = current_epoch + 1 if current_epoch else 1
            prev_chat_id = None

        elif transition == "hop":
            if current is None or current_state != "open":
                raise SeatLedgerError(
                    code="seat_transition_invalid",
                    message="hop requires open current seat",
                    body={"current": _row_to_dict(current)},
                )
            if expected_epoch is not None and int(expected_epoch) != current_epoch:
                raise SeatLedgerError(
                    code="seat_epoch_conflict",
                    message="expected_epoch does not match current",
                    body={"current": _row_to_dict(current), "expected_epoch": expected_epoch},
                )
            norm_url = _normalize_chat_url(chat_url)
            if norm_url is None:
                raise SeatLedgerError(
                    code="seat_transition_invalid",
                    message="hop requires successor chat_url",
                    body={},
                )
            chat_id = _chat_id_for_url(norm_url)
            old_chat_id = current["chat_id"]
            if str(current["chat_url"] or "") == norm_url:
                raise SeatLedgerError(
                    code="seat_transition_invalid",
                    message="hop requires a different chat_url",
                    body={"current": _row_to_dict(current)},
                )
            state = "open"
            next_epoch = current_epoch + 1
            prev_chat_id = str(old_chat_id) if old_chat_id else None

        elif transition == "rebind":
            if current is None:
                raise SeatLedgerError(
                    code="seat_transition_invalid",
                    message="rebind requires a current row",
                    body={},
                )
            if expected_epoch is not None and int(expected_epoch) != current_epoch:
                raise SeatLedgerError(
                    code="seat_epoch_conflict",
                    message="expected_epoch does not match current",
                    body={"current": _row_to_dict(current), "expected_epoch": expected_epoch},
                )
            if current_state not in ("open", "contested"):
                raise SeatLedgerError(
                    code="seat_transition_invalid",
                    message="rebind requires open or contested current",
                    body={"current": _row_to_dict(current)},
                )
            if contested:
                state = "contested"
                norm_url = None
                chat_id = None
            else:
                state = "open"
                norm_url = _normalize_chat_url(chat_url)
                if norm_url is None:
                    raise SeatLedgerError(
                        code="seat_transition_invalid",
                        message="rebind requires chat_url unless contested",
                        body={},
                    )
                chat_id = _chat_id_for_url(norm_url)
            next_epoch = current_epoch + 1
            prev_chat_id = (
                str(current["chat_id"]) if current["chat_id"] is not None else None
            )

        elif transition == "close":
            if current is None:
                raise SeatLedgerError(
                    code="seat_transition_invalid",
                    message="close requires a current row",
                    body={},
                )
            if current_state == "closed":
                raise SeatLedgerError(
                    code="seat_transition_invalid",
                    message="lane already closed",
                    body={"current": _row_to_dict(current)},
                )
            if expected_epoch is not None and int(expected_epoch) != current_epoch:
                raise SeatLedgerError(
                    code="seat_epoch_conflict",
                    message="expected_epoch does not match current",
                    body={"current": _row_to_dict(current), "expected_epoch": expected_epoch},
                )
            state = "closed"
            norm_url = None
            chat_id = None
            next_epoch = current_epoch + 1
            prev_chat_id = (
                str(current["chat_id"]) if current["chat_id"] is not None else None
            )

        elif transition == "backfill":
            if current is not None:
                raise SeatLedgerError(
                    code="seat_transition_invalid",
                    message="backfill refused when ledger has rows",
                    body={"current": _row_to_dict(current)},
                )
            if contested:
                state = "contested"
                norm_url = None
                chat_id = None
            else:
                state = "open"
                norm_url = _normalize_chat_url(chat_url)
                if norm_url is None:
                    raise SeatLedgerError(
                        code="seat_transition_invalid",
                        message="backfill requires chat_url unless contested",
                        body={},
                    )
                chat_id = _chat_id_for_url(norm_url)
            next_epoch = int(expected_epoch or 1)
            prev_chat_id = None
        else:
            raise SeatLedgerError(
                code="seat_transition_invalid",
                message=f"Unhandled transition {transition}",
                body={},
            )

        if state == "open" and chat_id:
            holder = _holder_of_chat(conn, chat_id=chat_id)
            if holder is not None and str(holder["thread_id"]) != thread_id:
                raise SeatLedgerError(
                    code="seat_chat_held",
                    message="chat already open on another lane",
                    body={
                        "holding_thread_id": holder["thread_id"],
                        "chat_id": chat_id,
                        "current": _row_to_dict(holder),
                    },
                )

        ts = now()
        cur = conn.execute(
            "INSERT INTO thread_seat_ledger ("
            "thread_id, epoch, transition, state, chat_id, chat_url, "
            "prev_chat_id, actor, evidence, request_id, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                thread_id,
                next_epoch,
                transition,
                state,
                chat_id,
                norm_url if state == "open" else None,
                prev_chat_id,
                actor,
                evidence_json,
                request_id,
                ts,
            ),
        )
        row_id = int(cur.lastrowid)

        if state == "contested":
            global _contested_wake_quarantine_count
            _contested_wake_quarantine_count += 1

    from ..events.seat_transition import emit_seat_transition

    emit_seat_transition(
        thread_id=thread_id,
        epoch=next_epoch,
        transition=transition,
        state=state,
        chat_id=chat_id,
        actor=actor,
        request_id=request_id,
    )

    result = {
        "id": row_id,
        "thread_id": thread_id,
        "epoch": next_epoch,
        "transition": transition,
        "state": state,
        "chat_id": chat_id,
        "chat_url": norm_url if state == "open" else None,
        "prev_chat_id": prev_chat_id,
        "actor": actor,
        "evidence": evidence_json,
        "request_id": request_id,
        "created_at": ts,
        "idempotent_replay": False,
    }
    return result


def current_seat(*, thread_id: str) -> dict[str, Any]:
    thread_id = normalize_thread_id(thread_id)
    if get_thread(thread_id) is None:
        raise LookupError(f"Thread {thread_id} not found")
    with connect() as conn:
        row = _latest_row(conn, thread_id=thread_id)
    if row is None:
        return {
            "thread_id": thread_id,
            "state": "none",
            "epoch": None,
            "transition": None,
            "chat_id": None,
            "chat_url": None,
            "since": None,
        }
    data = _row_to_dict(row)
    assert data is not None
    return {
        "thread_id": thread_id,
        "state": data["state"],
        "epoch": data["epoch"],
        "transition": data["transition"],
        "chat_id": data["chat_id"],
        "chat_url": data["chat_url"],
        "prev_chat_id": data.get("prev_chat_id"),
        "actor": data["actor"],
        "since": data["created_at"],
    }


def seat_history(*, thread_id: str, limit: int = 200) -> list[dict[str, Any]]:
    thread_id = normalize_thread_id(thread_id)
    if get_thread(thread_id) is None:
        raise LookupError(f"Thread {thread_id} not found")
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM thread_seat_ledger WHERE thread_id = ? "
            "ORDER BY epoch ASC LIMIT ?",
            (thread_id, max(1, min(limit, 1000))),
        ).fetchall()
    return [_row_to_dict(r) for r in rows if r is not None]  # type: ignore[misc]


def list_open_seats(*, state: str | None = None) -> list[dict[str, Any]]:
    state_filter = (state or "open").strip().lower()
    if state_filter not in _STATES and state_filter != "all":
        state_filter = "open"
    with connect() as conn:
        if state_filter == "all":
            rows = conn.execute(
                """
                SELECT tsl.*
                FROM thread_seat_ledger tsl
                INNER JOIN (
                    SELECT thread_id, MAX(epoch) AS max_epoch
                    FROM thread_seat_ledger
                    GROUP BY thread_id
                ) cur ON tsl.thread_id = cur.thread_id AND tsl.epoch = cur.max_epoch
                ORDER BY tsl.thread_id
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT tsl.*
                FROM thread_seat_ledger tsl
                INNER JOIN (
                    SELECT thread_id, MAX(epoch) AS max_epoch
                    FROM thread_seat_ledger
                    GROUP BY thread_id
                ) cur ON tsl.thread_id = cur.thread_id AND tsl.epoch = cur.max_epoch
                WHERE tsl.state = ?
                ORDER BY tsl.thread_id
                """,
                (state_filter,),
            ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        data = _row_to_dict(row)
        if data is not None:
            out.append(data)
    return out


def _cse_fold_mismatch(conn: sqlite3.Connection, thread_id: str, chat_url: str | None) -> bool:
    row = conn.execute(
        "SELECT cse_chat_url FROM thread_cse_associations "
        "WHERE thread_id = ? ORDER BY id DESC LIMIT 1",
        (thread_id,),
    ).fetchone()
    if row is None:
        return chat_url is not None
    folded = _normalize_chat_url(row["cse_chat_url"])
    seat_url = _normalize_chat_url(chat_url)
    return folded != seat_url


def _subscription_drift(conn: sqlite3.Connection, thread_id: str) -> bool:
    """True when a CSE-scoped subscription agent_id disagrees with ledger seat chat."""
    seat = _latest_row(conn, thread_id=thread_id)
    if seat is None or seat["state"] != "open" or not seat["chat_id"]:
        return False
    seat_chat_id = str(seat["chat_id"])
    rows = conn.execute(
        "SELECT agent_id FROM agent_endpoint_subscriptions "
        "WHERE thread_scope = ? AND actuation IS NULL",
        (thread_id,),
    ).fetchall()
    cse_pat = re.compile(r"cse_[A-Za-z0-9]+")
    for row in rows:
        agent_id = str(row["agent_id"] or "")
        if not agent_id:
            continue
        match = cse_pat.search(agent_id)
        if match and match.group(0) != seat_chat_id:
            return True
    return False


def seat_drift_summary() -> dict[str, Any]:
    """GET-only divergence counters: ledger vs CSE fold vs subscriptions vs GIW."""
    cse_mismatch = 0
    subscription_mismatch = 0
    giw_mismatch = 0
    giw_probe: str = "skipped"
    mismatched_threads: list[str] = []

    with connect() as conn:
        open_rows = list_open_seats(state="open")
        for seat in open_rows:
            tid = str(seat["thread_id"])
            if _cse_fold_mismatch(conn, tid, seat.get("chat_url")):
                cse_mismatch += 1
                mismatched_threads.append(tid)
            if _subscription_drift(conn, tid):
                subscription_mismatch += 1

    # GIW holder rows live in the worker's store; shadow drift records availability only.
    giw_probe = "unavailable"

    return {
        "cse_association_mismatch_count": cse_mismatch,
        "subscription_mismatch_count": subscription_mismatch,
        "giw_holder_mismatch_count": giw_mismatch,
        "giw_probe": giw_probe,
        "shadow_hook_error_count": shadow_hook_error_count(),
        "contested_wake_quarantine_count": contested_wake_quarantine_count(),
        "mismatched_thread_ids": sorted(set(mismatched_threads)),
        "open_seat_count": len(list_open_seats(state="open")),
    }


def shadow_hook_associate_cse(
    *,
    thread_id: str,
    url: str,
    bound_by: str | None,
    prior_url: str | None,
    appended: bool,
    association_id: int,
) -> None:
    """Fail-open shadow write after a successful ``associate_cse`` append."""
    if not seat_ledger_shadow_writes_enabled():
        return
    if not appended:
        return
    norm_url = _normalize_chat_url(url)
    if norm_url is None:
        return
    prior_norm = _normalize_chat_url(prior_url) if prior_url else None
    if prior_norm == norm_url:
        return
    if prior_norm is not None and (bound_by or "") != HOP_SEATED_BOUND_BY:
        return

    request_id = f"shadow.associate_cse.{association_id}"
    try:
        if prior_norm is None:
            transition_seat(
                thread_id=thread_id,
                transition="birth",
                chat_url=norm_url,
                actor="shadow.associate_cse",
                evidence={"association_id": association_id, "bound_by": bound_by},
                request_id=request_id,
            )
        elif (bound_by or "") == HOP_SEATED_BOUND_BY:
            current = current_seat(thread_id=thread_id)
            expected = current.get("epoch")
            transition_seat(
                thread_id=thread_id,
                transition="hop",
                chat_url=norm_url,
                expected_epoch=int(expected) if expected is not None else None,
                actor="shadow.associate_cse",
                evidence={"association_id": association_id, "bound_by": bound_by},
                request_id=request_id,
            )
    except SeatLedgerError:
        raise


def shadow_hook_thread_close(*, thread_id: str) -> None:
    if not seat_ledger_shadow_writes_enabled():
        return
    current = current_seat(thread_id=thread_id)
    if current.get("state") in (None, "none", "closed"):
        return
    request_id = f"shadow.thread_close.{thread_id}.{current.get('epoch')}"
    try:
        transition_seat(
            thread_id=thread_id,
            transition="close",
            expected_epoch=int(current["epoch"]) if current.get("epoch") else None,
            actor="shadow.thread_close",
            request_id=request_id,
        )
    except SeatLedgerError:
        raise

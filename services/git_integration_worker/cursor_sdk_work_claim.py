"""House occupancy claims on the cursor-sdk dispatch ledger.

One ``claimed`` row per ``(work_key, holder_agent, thread_id)``. Callers are the
claim routes and the admit gate. A claim is not a writer: ``read_only=1``,
``contract='claim'``, and it is absent from ``_ACTIVE_IDENTITY_STATUSES``.
Kind ``dispatch`` is the admit row itself and has no claim row and no TTL.
Expiry cancels the row and posts one FACT; a failed post does not roll back.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from universal_logging import get_logger

from services.git_integration_worker.cursor_bus import CursorBusClient
from services.git_integration_worker.cursor_dispatch_ledger import (
    _ACTIVE_IDENTITY_STATUSES,
    CursorDispatchLedger,
    _emit_impresario_lease_acquired,
    _holder_surface_from_record,
    _lease_epoch_from_row,
    bump_lease_epoch_conn,
)
from services.git_integration_worker.cursor_sdk_conductor_park_gate import (
    OPEN_RESTART_PARK_SQL,
    open_parks,
)
from services.git_integration_worker.cursor_sdk_work_key_gate import (
    holder_kind_from_row,
)

logger = get_logger(__name__)

CLAIM_STATUS = "claimed"
CLAIM_CONTRACT = "claim"
DEFAULT_TTL_S = 7200
MIN_TTL_S = 60
MAX_TTL_S = 86400
FACT_THREAD_ID = "15851"
FACT_TO = "gc1"
FACT_FROM = "git-integration-worker"
OCCUPANCY_LABEL = "occupancy, not progress"
_PIN_TOKENS = (
    "ROW_PINNED",
    "HOLD_MERGE",
    "OPERATOR_GATE",
    "CONFIRM_PENDING",
    "CONSULT_PENDING",
)
_ACTING_DISPATCH = ("queued", "admitted", "running")
_RELEASE_STATUS = {
    "completed": "completed",
    "failed": "failed",
    "withdrawn": "cancelled",
}

Poster = Callable[[dict[str, Any]], None]


class WorkKeyHeld(Exception):  # noqa: N818 — HTTP 409 WORK_KEY_HELD (spec name)
    """Another live holder already occupies this work_key.

    The claim route maps this to HTTP 409 ``WORK_KEY_HELD``. Attributes are the
    holder projection the caller needs to steer or wait.
    """

    def __init__(self, *, work_key: str, holder: dict[str, Any]) -> None:
        """Attach the conflicting holder projection for the 409 envelope."""
        self.work_key = work_key
        self.holder = holder
        super().__init__(
            f"work_key {work_key!r} held by {holder.get('holder_dispatch_id')!r}"
        )

    def as_dict(self) -> dict[str, Any]:
        """409 body fields: work_key plus the holder projection."""
        return {"work_key": self.work_key, **self.holder}


class ClaimError(Exception):
    """Typed refusal from release: unknown id, wrong holder, or bad outcome.

    ``http_status`` is 404, 409, or 422. The claim route copies ``code`` into
    the JSON body and does not invent a second status mapping.
    """

    def __init__(self, *, code: str, http_status: int, message: str) -> None:
        """Store the worker error code and HTTP status for the claim route."""
        self.code = code
        self.http_status = http_status
        super().__init__(message)


def claim_id_for(*, work_key: str, holder_agent: str, thread_id: str) -> str:
    """Stable claim id: ``claim-`` plus 16 hex chars of the holder triple."""
    digest = hashlib.sha256(
        f"{work_key}|{holder_agent}|{thread_id}".encode()
    ).hexdigest()[:16]
    return f"claim-{digest}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _clamp_ttl(ttl_s: int) -> int:
    if ttl_s < MIN_TTL_S or ttl_s > MAX_TTL_S:
        raise ValueError(
            f"ttl_s must be between {MIN_TTL_S} and {MAX_TTL_S}, got {ttl_s}"
        )
    return ttl_s


def _expiry(now: str, ttl_s: int) -> str:
    return (_parse_iso(now) + timedelta(seconds=ttl_s)).isoformat()


def _record(holder_agent: str, request_turn: int | None) -> str:
    payload: dict[str, Any] = {
        "claim_kind": "house",
        "holder_agent": holder_agent,
    }
    if request_turn is not None:
        payload["request_turn"] = request_turn
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _holder_from_row(row: Any, *, now: str) -> dict[str, Any]:
    status = str(row["status"])
    kind = "claim" if status == CLAIM_STATUS else holder_kind_from_row(row)
    if status in _ACTING_DISPATCH or status == "parked_waiting":
        kind = "dispatch" if status != CLAIM_STATUS else "claim"
    if status == CLAIM_STATUS:
        kind = "claim"
    elif kind == "claim":
        kind = "dispatch"
    claimed_at = row["queued_at"] or row["started_at"]
    expires = row["claim_expires_at"] if "claim_expires_at" in row.keys() else None
    if status != CLAIM_STATUS:
        expires = row["park_expires_at"] if "park_expires_at" in row.keys() else None
    return {
        "id": row["dispatch_id"],
        "kind": kind if status == CLAIM_STATUS else "dispatch",
        "status": status,
        "thread_id": row["thread_id"],
        "caller_agent": row["caller_agent"],
        "claimed_at": claimed_at,
        "expires_at": expires,
        "holder_dispatch_id": row["dispatch_id"],
        "holder_thread_id": row["thread_id"],
        "holder_kind": "claim" if status == CLAIM_STATUS else holder_kind_from_row(row),
        "holder_agent": row["caller_agent"],
        "as_of": now,
    }


def _conflict_holder(row: Any) -> dict[str, Any]:
    status = str(row["status"])
    kind = "claim" if status == CLAIM_STATUS else holder_kind_from_row(row)
    return {
        "holder_dispatch_id": row["dispatch_id"],
        "holder_thread_id": row["thread_id"],
        "holder_kind": kind,
        "holder_agent": row["caller_agent"],
        "claimed_at": row["queued_at"] or row["started_at"],
        "expires_at": row["claim_expires_at"],
    }


def _find_blocker(conn: Any, *, work_key: str, exclude_id: str, now: str) -> Any:
    return conn.execute(
        "SELECT dispatch_id, thread_id, status, contract, packet_kind, "
        "record_json, caller_agent, queued_at, started_at, claim_expires_at "
        "FROM cursor_sdk_dispatches "
        "WHERE work_key=? AND dispatch_id<>? "
        f"AND (status IN ({_ACTIVE_IDENTITY_STATUSES}) "
        f"OR {OPEN_RESTART_PARK_SQL} "
        "OR (status='claimed' AND claim_expires_at > ?)) "
        "ORDER BY CASE WHEN status='claimed' THEN 1 ELSE 0 END "
        "LIMIT 1",
        (work_key, exclude_id, now, now),
    ).fetchone()


def claim(
    work_key: str,
    holder_agent: str,
    thread_id: str,
    ttl_s: int = DEFAULT_TTL_S,
    request_turn: int | None = None,
    *,
    ledger: CursorDispatchLedger | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Insert, renew, or revive one house claim inside ``BEGIN IMMEDIATE``.

    A re-call by the same triple while the row is unexpired extends
    ``claim_expires_at`` (the house poll). Any other live holder raises
    ``WorkKeyHeld``. An explicit claim is always kind ``house``.
    """
    ttl = _clamp_ttl(int(ttl_s))
    led = ledger or CursorDispatchLedger.instance()
    stamp = now or _now_iso()
    expires = _expiry(stamp, ttl)
    claim_id = claim_id_for(
        work_key=work_key, holder_agent=holder_agent, thread_id=thread_id
    )
    record = _record(holder_agent, request_turn)
    with led._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        blocker = _find_blocker(
            conn, work_key=work_key, exclude_id=claim_id, now=stamp
        )
        if blocker is not None:
            raise WorkKeyHeld(work_key=work_key, holder=_conflict_holder(blocker))
        own = conn.execute(
            "SELECT dispatch_id, status, claim_expires_at, queued_at, thread_id, "
            "caller_agent, started_at, contract, packet_kind, record_json "
            "FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (claim_id,),
        ).fetchone()
        renewed = False
        if own is not None and own["status"] == CLAIM_STATUS:
            own_exp = own["claim_expires_at"]
            if own_exp and own_exp > stamp:
                conn.execute(
                    "UPDATE cursor_sdk_dispatches SET claim_expires_at=? "
                    "WHERE dispatch_id=? AND status='claimed'",
                    (expires, claim_id),
                )
                renewed = True
            else:
                revive_epoch = bump_lease_epoch_conn(conn, work_key=work_key)
                conn.execute(
                    "UPDATE cursor_sdk_dispatches SET status='claimed', "
                    "terminal_status=NULL, terminal_at=NULL, claim_expires_at=?, "
                    "lease_epoch=?, "
                    "record_json=json_remove(COALESCE(record_json,'{}'), "
                    "'$.claim_release'), caller_agent=?, thread_id=?, work_key=? "
                    "WHERE dispatch_id=?",
                    (
                        expires,
                        revive_epoch,
                        holder_agent,
                        thread_id,
                        work_key,
                        claim_id,
                    ),
                )
        elif own is not None:
            conn.execute(
                "UPDATE cursor_sdk_dispatches SET status='claimed', "
                "terminal_status=NULL, terminal_at=NULL, claim_expires_at=?, "
                "contract='claim', packet_kind='claim', identity_class='house', "
                "read_only=1, resolved_model='none', fingerprint=?, "
                "work_fingerprint=NULL, caller_agent=?, thread_id=?, work_key=?, "
                "record_json=?, queued_at=? WHERE dispatch_id=?",
                (
                    expires,
                    claim_id,
                    holder_agent,
                    thread_id,
                    work_key,
                    record,
                    stamp,
                    claim_id,
                ),
            )
        else:
            lease_epoch = bump_lease_epoch_conn(conn, work_key=work_key)
            conn.execute(
                "INSERT INTO cursor_sdk_dispatches "
                "(dispatch_id, fingerprint, thread_id, caller_agent, "
                " resolved_model, message_present, status, record_json, "
                " contract, read_only, queued_at, work_key, work_fingerprint, "
                " identity_class, packet_kind, claim_expires_at, lease_epoch) "
                "VALUES (?, ?, ?, ?, 'none', 0, 'claimed', ?, 'claim', 1, ?, ?, "
                "NULL, 'house', 'claim', ?, ?)",
                (
                    claim_id,
                    claim_id,
                    thread_id,
                    holder_agent,
                    record,
                    stamp,
                    work_key,
                    expires,
                    lease_epoch,
                ),
            )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (claim_id,),
        ).fetchone()
    assert row is not None
    if not renewed and row["status"] == CLAIM_STATUS:
        epoch = _lease_epoch_from_row(row)
        if epoch is not None:
            _emit_impresario_lease_acquired(
                work_key=work_key,
                lease_epoch=epoch,
                dispatch_id=claim_id,
            )
    holder = _holder_from_row(row, now=stamp)
    return {
        "claim_id": claim_id,
        "renewed": renewed,
        "holder": {
            "id": holder["id"],
            "kind": "claim",
            "status": holder["status"],
            "thread_id": holder["thread_id"],
            "caller_agent": holder["caller_agent"],
            "claimed_at": holder["claimed_at"],
            "expires_at": holder["expires_at"],
        },
    }


def release(
    claim_id: str,
    holder_agent: str,
    outcome: str,
    *,
    ledger: CursorDispatchLedger | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """End a claimed row when the caller is the holder.

    ``completed`` / ``failed`` / ``withdrawn`` map to those terminal statuses
    (withdrawn → ``cancelled``). A repeat of the same outcome is 200 with
    ``already_released``. Unknown id is 404; a different holder or a
    non-claimed status is 409.
    """
    if outcome not in _RELEASE_STATUS:
        raise ClaimError(
            code="INVALID_OUTCOME",
            http_status=422,
            message="outcome must be completed, failed, or withdrawn",
        )
    led = ledger or CursorDispatchLedger.instance()
    stamp = now or _now_iso()
    status = _RELEASE_STATUS[outcome]
    with led._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT dispatch_id, status, caller_agent, record_json "
            "FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (claim_id,),
        ).fetchone()
        if row is None:
            raise ClaimError(
                code="CLAIM_NOT_FOUND",
                http_status=404,
                message=f"claim {claim_id!r} not found",
            )
        record = {}
        try:
            record = json.loads(row["record_json"] or "{}")
        except json.JSONDecodeError:
            record = {}
        if not isinstance(record, dict):
            record = {}
        prior = record.get("claim_release")
        if row["status"] != CLAIM_STATUS:
            if prior == outcome and row["caller_agent"] == holder_agent:
                conn.commit()
                return {
                    "claim_id": claim_id,
                    "status": row["status"],
                    "outcome": outcome,
                    "already_released": True,
                }
            raise ClaimError(
                code="CLAIM_NOT_HELD",
                http_status=409,
                message="claim is not held by this caller",
            )
        if row["caller_agent"] != holder_agent:
            raise ClaimError(
                code="CLAIM_NOT_HELD",
                http_status=409,
                message="claim is held by a different agent",
            )
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET status=?, terminal_status=?, "
            "terminal_at=?, record_json=json_set(COALESCE(record_json,'{}'), "
            "'$.claim_release', ?) WHERE dispatch_id=? AND status='claimed' "
            "AND caller_agent=?",
            (status, status, stamp, outcome, claim_id, holder_agent),
        )
        conn.commit()
    return {
        "claim_id": claim_id,
        "status": status,
        "outcome": outcome,
        "already_released": False,
    }


def fact_body_for(row: dict[str, Any]) -> str:
    """One-line FACT body for an expired house claim. No second sentence."""
    return (
        f"FACT: claim {row['claim_id']} on {row['work_key']} "
        f"(holder {row['holder_agent']}, thread {row['thread_id']}) "
        f"expired at {row['expired_at']}; status cancelled, claim_release=expired."
    )


def post_claim_expired_fact(row: dict[str, Any]) -> None:
    """Post one FACT turn with the same bus client that posts PARKED turns.

    Thread 15851, to ``gc1``, from ``git-integration-worker``. A transport
    failure is the caller's problem; ``expire_claims`` logs and continues.
    """
    claim_id = row["claim_id"]
    body = str(row.get("body") or fact_body_for(row))
    subject = str(row.get("subject") or f"FACT|claim-expired|{row['work_key']}")

    async def _send() -> None:
        client = CursorBusClient()
        result = await client.reply(
            thread_id=str(row.get("fact_thread") or FACT_THREAD_ID),
            to_agent=str(row.get("fact_to") or FACT_TO),
            from_agent=str(row.get("fact_from") or FACT_FROM),
            subject=subject,
            body=body,
        )
        if result.status_code >= 400:
            logger.warning(
                "claim-expired FACT post failed claim_id=%s status=%s",
                claim_id,
                result.status_code,
            )

    asyncio.run(_send())


def expire_claims(
    now: str | None = None,
    *,
    ledger: CursorDispatchLedger | None = None,
    poster: Poster | None = None,
) -> list[dict[str, Any]]:
    """Cancel unexpired-deadline claim rows and post one FACT each.

    The UPDATE commits before any post. A poster exception logs a warning and
    leaves the cancelled row in place.
    """
    led = ledger or CursorDispatchLedger.instance()
    stamp = now or _now_iso()
    send = poster or post_claim_expired_fact
    with led._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "UPDATE cursor_sdk_dispatches SET status='cancelled', "
            "terminal_status='cancelled', terminal_at=?, "
            "record_json=json_set(COALESCE(record_json,'{}'), "
            "'$.claim_release', 'expired') "
            "WHERE status='claimed' AND claim_expires_at <= ? "
            "RETURNING dispatch_id, work_key, caller_agent, thread_id",
            (stamp, stamp),
        ).fetchall()
        conn.commit()
    expired: list[dict[str, Any]] = []
    for row in rows:
        item = {
            "claim_id": row["dispatch_id"],
            "work_key": row["work_key"],
            "holder_agent": row["caller_agent"],
            "thread_id": row["thread_id"],
            "expired_at": stamp,
            "fact_thread": FACT_THREAD_ID,
            "fact_to": FACT_TO,
            "fact_from": FACT_FROM,
        }
        item["subject"] = f"FACT|claim-expired|{item['work_key']}"
        item["body"] = fact_body_for(item)
        expired.append(item)
        try:
            send(item)
        except Exception as exc:  # noqa: BLE001 — expiry must stick
            logger.warning(
                "claim-expired FACT post failed claim_id=%s err=%s",
                item["claim_id"],
                exc,
            )
    return expired


def _stop_token(record_json: str | None) -> str | None:
    try:
        data = json.loads(record_json or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("closeout_stop_tokens")
    if not isinstance(raw, list):
        return None
    tokens = {str(item).upper() for item in raw}
    for name in _PIN_TOKENS:
        if name in tokens:
            return name
    return None


def _projection_holder(row: Any) -> dict[str, Any]:
    status = str(row["status"])
    kind = "claim" if status == CLAIM_STATUS else "dispatch"
    expires = row["claim_expires_at"] if status == CLAIM_STATUS else row["park_expires_at"]
    holder: dict[str, Any] = {
        "id": row["dispatch_id"],
        "kind": kind,
        "status": status,
        "thread_id": row["thread_id"],
        "caller_agent": row["caller_agent"],
        "claimed_at": row["queued_at"] or row["started_at"],
        "expires_at": expires,
    }
    surface = _holder_surface_from_record(row["record_json"])
    if surface is not None:
        holder["holder_surface"] = surface
    epoch = _lease_epoch_from_row(row)
    if epoch is not None:
        holder["lease_epoch"] = epoch
    return holder


def occupancy(
    work_key: str,
    now: str | None = None,
    *,
    ledger: CursorDispatchLedger | None = None,
) -> dict[str, Any]:
    """Occupancy of one work_key. Never a progress or landed reading.

    Precedence: parked (parked_waiting, open restart park, open budget park),
    then acting (queued/admitted/running, or an unexpired claim), then pinned
    from the latest non-claim terminal closeout tokens, else idle. Pinned does
    not block admit or claim.
    """
    led = ledger or CursorDispatchLedger.instance()
    stamp = now or _now_iso()
    with led._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE work_key=? "
            "ORDER BY rowid ASC",
            (work_key,),
        ).fetchall()
        budget = open_parks(
            conn,
            work_key=work_key,
            thread_id=None,
            now=stamp,
            kinds=frozenset({"budget"}),
        )
        restart = open_parks(
            conn,
            work_key=work_key,
            thread_id=None,
            now=stamp,
            kinds=frozenset({"restart"}),
        )
    by_id = {row["dispatch_id"]: row for row in rows}

    def _pack(state: str, row: Any, stop: str | None) -> dict[str, Any]:
        return {
            "work_key": work_key,
            "state": state,
            "label": OCCUPANCY_LABEL,
            "holder": None if row is None else _projection_holder(row),
            "stop": stop,
        }

    for row in rows:
        if row["status"] == "parked_waiting":
            return _pack("parked", row, None)
    if restart:
        parked_row = by_id.get(restart[0].dispatch_id)
        if parked_row is not None:
            return _pack("parked", parked_row, None)
    if budget:
        parked_row = by_id.get(budget[0].dispatch_id)
        if parked_row is not None:
            return _pack("parked", parked_row, None)
    for row in rows:
        if row["status"] in _ACTING_DISPATCH:
            return _pack("acting", row, None)
    for row in rows:
        exp = row["claim_expires_at"]
        if row["status"] == CLAIM_STATUS and exp and exp > stamp:
            return _pack("acting", row, None)
    latest_row = None
    if rows:
        with led._connect() as conn:
            latest_row = conn.execute(
                "SELECT * FROM cursor_sdk_dispatches WHERE work_key=? "
                "AND status IN ('completed','failed','cancelled') "
                "AND COALESCE(contract,'') != 'claim' "
                "ORDER BY terminal_at DESC, rowid DESC LIMIT 1",
                (work_key,),
            ).fetchone()
    if latest_row is not None:
        stop = _stop_token(latest_row["record_json"])
        if stop is not None:
            return _pack("pinned", None, stop)
    return _pack("idle", None, None)

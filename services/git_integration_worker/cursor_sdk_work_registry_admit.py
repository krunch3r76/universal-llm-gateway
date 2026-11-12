"""Best-effort work-registry stamps at team_dispatch admit and terminal closeout."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

from universal_logging import get_logger

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_claim_address import (
    AddressValidationError,
    normalize_address,
)

logger = get_logger(__name__)

_FRONTIER_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_FRONTIER_LIVE_STATUSES = ("queued", "admitted", "running")


def _frontier_wall_margin_s() -> int:
    return int(os.environ.get("CURSOR_REGISTRY_FRONTIER_WALL_MARGIN_S", "900"))


def _frontier_default_ttl_s() -> int:
    return int(os.environ.get("CURSOR_REGISTRY_FRONTIER_TTL_S", "14400"))


def _frontier_reap_threshold_s(record_json: str | None, *, ttl_s: int) -> int:
    """Seconds a frontier row may stay live before the TTL reaper may fail it."""
    margin = _frontier_wall_margin_s()
    try:
        rec = json.loads(record_json or "{}")
    except json.JSONDecodeError:
        rec = {}
    if not isinstance(rec, dict):
        rec = {}
    raw_wall = rec.get("max_wall_s")
    if raw_wall is None:
        return ttl_s
    try:
        wall = int(raw_wall)
    except (TypeError, ValueError):
        return ttl_s
    return max(ttl_s, wall + margin)


WORK_REGISTRY_RULE = (
    'WORK REGISTRY RULE: Before acting on a work_key, read team_dispatch(op="registry", '
    "work_key_prefix=<key>). If another live row holds it, coordinate on its thread "
    "instead of duplicating. If you are not already auto-recorded (team_dispatch generate "
    'auto-records with an address), claim it: team_dispatch(op="claim", work_key, thread, '
    'seat_name, address). Keep it current with op="claim_update" (work_status, '
    "address_patch, note); finish with work_status=landed or abandoned."
)

_REGISTRY_NEXT_TEMPLATE = (
    "Work registry: read team_dispatch(op=registry, work_key_prefix=<key>) before starting "
    "work on a work_key; claim with op=claim (seat_name, address) and keep it current with "
    "op=claim_update (acting-row updates require the bridge caller_dispatch_id stamp — "
    "unstamped callers get 409); this dispatch was auto-recorded as {row_id}."
)


def registry_next_hint(*, row_id: str) -> str:
    """Operator-facing _next line after generate/to_thread admit."""
    rid = str(row_id or "").strip() or "(unknown)"
    return _REGISTRY_NEXT_TEMPLATE.format(row_id=rid)


def resolve_registry_seat_name(
    *,
    caller_agent: str | None,
    seat: str | None = None,
    role: str | None = None,
) -> str | None:
    """Prefer explicit seat, then caller_agent, then functional role."""
    for raw in (seat, caller_agent, role):
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


def build_cursor_sdk_registry_address(
    *,
    dispatch_id: str,
    worker_thread: str | None,
    worktree: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "kind": "cursor_sdk",
        "dispatch_id": str(dispatch_id).strip(),
    }
    if worker_thread and str(worker_thread).strip():
        out["worker_thread"] = str(worker_thread).strip()
    if worktree and str(worktree).strip():
        out["worktree"] = str(worktree).strip()
    return out


def build_api_registry_address(*, thread_id: str) -> dict[str, Any]:
    return {"kind": "api_role", "thread_id": str(thread_id).strip()}


def build_cdp_registry_address(
    *,
    thread_id: str,
    chat_url: str | None = None,
    registration_id: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {"kind": "cdp", "thread_id": str(thread_id).strip()}
    if chat_url and str(chat_url).strip():
        out["chat_url"] = str(chat_url).strip()
    if registration_id and str(registration_id).strip():
        out["registration_id"] = str(registration_id).strip()
    return out


def build_cursor_ide_registry_address(
    *,
    gui_host: str,
    tab: str | None = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "kind": "cursor_ide",
        "gui_host": str(gui_host).strip(),
    }
    if tab and str(tab).strip():
        out["tab"] = str(tab).strip()
    if agent_id and str(agent_id).strip():
        out["agent_id"] = str(agent_id).strip()
    return out


def _merge_registry_record(
    existing: dict[str, Any],
    *,
    seat_name: str | None,
    address: dict[str, Any] | None,
    work_status: str | None,
) -> dict[str, Any]:
    merged = dict(existing)
    if seat_name and str(seat_name).strip():
        merged["seat_name"] = str(seat_name).strip()
    if address:
        prior = merged.get("address")
        if isinstance(prior, dict):
            merged["address"] = {**prior, **address}
        else:
            merged["address"] = dict(address)
    if work_status and str(work_status).strip():
        merged["work_status"] = str(work_status).strip()
    merged.setdefault("registry_auto_recorded", True)
    return merged


def stamp_registry_on_dispatch_row(
    *,
    dispatch_id: str,
    caller_agent: str | None = None,
    seat_name: str | None = None,
    address: dict[str, Any] | None = None,
    work_status: str | None = "active",
    work_key: str | None = None,
    ledger: CursorDispatchLedger | None = None,
) -> str | None:
    """Patch an existing dispatch row. Returns dispatch_id on success, else None."""
    led = ledger or CursorDispatchLedger.instance()
    norm_address: dict[str, Any] | None = None
    if address is not None:
        try:
            norm_address = normalize_address(address)
        except AddressValidationError as exc:
            logger.warning(
                "registry stamp skipped bad address dispatch_id=%s err=%s",
                dispatch_id,
                exc,
            )
            return None
    with led._connect() as conn:
        row = conn.execute(
            "SELECT dispatch_id, caller_agent, record_json, work_key "
            "FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            rec = json.loads(row["record_json"] or "{}")
        except json.JSONDecodeError:
            rec = {}
        if not isinstance(rec, dict):
            rec = {}
        merged = _merge_registry_record(
            rec,
            seat_name=seat_name,
            address=norm_address,
            work_status=work_status,
        )
        record_json = json.dumps(merged, sort_keys=True, separators=(",", ":"))
        caller = row["caller_agent"]
        if not caller and caller_agent and str(caller_agent).strip():
            caller = str(caller_agent).strip()
        wk = row["work_key"]
        if not wk and work_key and str(work_key).strip():
            wk = str(work_key).strip()
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET record_json=?, "
            "caller_agent=COALESCE(?, caller_agent), work_key=COALESCE(?, work_key) "
            "WHERE dispatch_id=?",
            (record_json, caller, wk, dispatch_id),
        )
        conn.commit()
    return dispatch_id


def upsert_frontier_registry_dispatch(
    *,
    dispatch_id: str,
    thread_id: str,
    caller_agent: str | None,
    seat_name: str | None,
    address: dict[str, Any],
    work_key: str | None = None,
    resolved_model: str = "none",
    contract: str | None = None,
    max_wall_s: int | None = None,
    prompt_bytes: int | None = None,
    ledger: CursorDispatchLedger | None = None,
) -> str | None:
    """Insert or refresh a registry projection row for non-GIW admits."""
    led = ledger or CursorDispatchLedger.instance()
    try:
        norm_address = normalize_address(address)
    except AddressValidationError as exc:
        logger.warning(
            "frontier registry upsert skipped bad address id=%s err=%s",
            dispatch_id,
            exc,
        )
        return None
    rec = _merge_registry_record(
        {},
        seat_name=seat_name,
        address=norm_address,
        work_status="active",
    )
    if max_wall_s is not None:
        rec["max_wall_s"] = int(max_wall_s)
    if prompt_bytes is not None:
        rec["prompt_bytes"] = int(prompt_bytes)
    record_json = json.dumps(rec, sort_keys=True, separators=(",", ":"))
    holder = (caller_agent or seat_name or "dispatch").strip()
    fp = str(dispatch_id).strip()
    with led._connect() as conn:
        existing = conn.execute(
            "SELECT dispatch_id, status FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (fp,),
        ).fetchone()
        if existing is not None:
            if str(existing["status"]) in _FRONTIER_TERMINAL_STATUSES:
                return fp
            conn.execute(
                "UPDATE cursor_sdk_dispatches SET record_json=?, thread_id=?, "
                "caller_agent=COALESCE(?, caller_agent), work_key=COALESCE(?, work_key) "
                "WHERE dispatch_id=? "
                "AND status IN ('queued','admitted','running')",
                (
                    record_json,
                    thread_id,
                    holder or None,
                    work_key,
                    fp,
                ),
            )
        else:
            from services.git_integration_worker.cursor_dispatch_ledger import _now

            stamp = _now()
            conn.execute(
                "INSERT INTO cursor_sdk_dispatches "
                "(dispatch_id, fingerprint, thread_id, caller_agent, resolved_model, "
                " message_present, status, record_json, contract, read_only, queued_at, "
                " work_key, identity_class, started_at) "
                "VALUES (?, ?, ?, ?, ?, 0, 'admitted', ?, ?, 1, ?, ?, 'declared', ?)",
                (
                    fp,
                    fp,
                    thread_id,
                    holder or None,
                    resolved_model,
                    record_json,
                    contract or "generate",
                    stamp,
                    work_key,
                    stamp,
                ),
            )
        conn.commit()
    return fp


def close_frontier_registry_row(
    dispatch_id: str,
    terminal_status: str,
    reason: str,
    *,
    harvest_chars: int | None = None,
    ledger: CursorDispatchLedger | None = None,
) -> dict[str, Any]:
    """Mark a frontier (api_role/cdp) registry projection row terminal."""
    fp = str(dispatch_id).strip()
    if not fp or terminal_status not in _FRONTIER_TERMINAL_STATUSES:
        return {"ok": False, "reason": "not_found"}
    led = ledger or CursorDispatchLedger.instance()
    from services.git_integration_worker.cursor_dispatch_ledger import _now

    now_stamp = _now()
    with led._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        json_set = "json_set(COALESCE(record_json, '{}'), '$.terminal_reason', ?)"
        params: list[Any] = [terminal_status, terminal_status, now_stamp, reason]
        if harvest_chars is not None:
            json_set = f"json_set({json_set}, '$.harvest_chars', ?)"
            params.append(int(harvest_chars))
        params.append(fp)
        cur = conn.execute(
            "UPDATE cursor_sdk_dispatches SET status=?, terminal_status=?, "
            f"terminal_at=?, record_json={json_set} "
            "WHERE dispatch_id=? "
            "AND status IN ('queued','admitted','running') "
            "AND COALESCE(read_only, 0)=1 "
            "AND json_extract(record_json, '$.address.kind') IN ('api_role', 'cdp')",
            tuple(params),
        )
        if cur.rowcount == 1:
            conn.execute(
                "INSERT OR IGNORE INTO cursor_dispatch_stop_service "
                "(stop_id, serviced_admit) VALUES (?, NULL)",
                (fp,),
            )
            conn.commit()
            stamp_registry_terminal_work_status(
                dispatch_id=fp,
                terminal_status=terminal_status,
                ledger=led,
            )
            return {"ok": True, "marked": True}
        row = conn.execute(
            "SELECT status, record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (fp,),
        ).fetchone()
        if row is None:
            tomb = json.dumps(
                {
                    "terminal_reason": reason,
                    "registry_auto_recorded": True,
                    "tombstone": True,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            conn.execute(
                "INSERT INTO cursor_sdk_dispatches "
                "(dispatch_id, fingerprint, thread_id, caller_agent, resolved_model, "
                " message_present, status, terminal_status, terminal_at, record_json, "
                " contract, read_only, queued_at, work_key, identity_class) "
                "VALUES (?, ?, '', NULL, 'none', 0, ?, ?, ?, ?, 'generate', 1, ?, NULL, "
                " 'declared')",
                (
                    fp,
                    fp,
                    terminal_status,
                    terminal_status,
                    now_stamp,
                    tomb,
                    now_stamp,
                ),
            )
            conn.commit()
            return {"ok": True, "marked": True}
        conn.commit()
        try:
            rec = json.loads(row["record_json"] or "{}")
        except json.JSONDecodeError:
            rec = {}
        if not isinstance(rec, dict):
            rec = {}
        addr = rec.get("address")
        if not isinstance(addr, dict) or addr.get("kind") not in ("api_role", "cdp"):
            return {"ok": False, "reason": "not_found"}
        return {"ok": True, "marked": False}


def bump_frontier_registry_heartbeat(
    dispatch_id: str,
    ledger: CursorDispatchLedger | None = None,
) -> dict[str, Any]:
    """Refresh ``last_heartbeat_at`` for a live CDP frontier registry row."""
    fp = str(dispatch_id or "").strip()
    if not fp:
        return {"ok": True, "bumped": False}
    led = ledger or CursorDispatchLedger.instance()
    from services.git_integration_worker.cursor_dispatch_ledger import _now

    now_stamp = _now()
    with led._connect() as conn:
        cur = conn.execute(
            "UPDATE cursor_sdk_dispatches SET last_heartbeat_at=? "
            "WHERE dispatch_id=? "
            "AND status IN ('queued','admitted','running') "
            "AND COALESCE(read_only, 0)=1 "
            "AND json_extract(record_json, '$.address.kind')='cdp'",
            (now_stamp, fp),
        )
        conn.commit()
    return {"ok": True, "bumped": cur.rowcount == 1}


def _frontier_reap_anchor_iso(
    *,
    record_json: str | None,
    started_at: str | None,
    queued_at: str | None,
    last_heartbeat_at: str | None,
) -> str | None:
    """Pick the timestamp the TTL reaper ages from (kind-specific)."""
    try:
        rec = json.loads(record_json or "{}")
    except json.JSONDecodeError:
        rec = {}
    if not isinstance(rec, dict):
        rec = {}
    addr = rec.get("address")
    kind = addr.get("kind") if isinstance(addr, dict) else None
    if kind == "cdp":
        return last_heartbeat_at or started_at or queued_at
    return started_at or queued_at


def reap_stale_frontier_registry_rows(
    ledger: CursorDispatchLedger | None = None,
    now: datetime | None = None,
    ttl_s: int | None = None,
) -> list[str]:
    """Backstop: fail frontier registry rows past per-row or default TTL."""
    led = ledger or CursorDispatchLedger.instance()
    effective_ttl = ttl_s if ttl_s is not None else _frontier_default_ttl_s()
    anchor = now or datetime.now(UTC)
    reaped: list[str] = []
    with led._connect() as conn:
        rows = conn.execute(
            "SELECT dispatch_id, record_json, started_at, queued_at, "
            "last_heartbeat_at "
            "FROM cursor_sdk_dispatches "
            "WHERE status IN ('queued','admitted','running') "
            "AND COALESCE(read_only, 0)=1 "
            "AND json_extract(record_json, '$.address.kind') IN ('api_role', 'cdp')",
        ).fetchall()
    for row in rows:
        dispatch_id = str(row["dispatch_id"])
        try:
            threshold_s = _frontier_reap_threshold_s(
                row["record_json"], ttl_s=effective_ttl
            )
            anchor_raw = _frontier_reap_anchor_iso(
                record_json=row["record_json"],
                started_at=row["started_at"],
                queued_at=row["queued_at"],
                last_heartbeat_at=row["last_heartbeat_at"],
            )
            if not anchor_raw:
                continue
            try:
                anchor_dt = datetime.fromisoformat(str(anchor_raw))
            except ValueError:
                logger.warning(
                    "frontier registry reaper skipped bad timestamp "
                    "dispatch_id=%s anchor=%r",
                    dispatch_id,
                    anchor_raw,
                )
                continue
            if anchor_dt.tzinfo is None:
                anchor_dt = anchor_dt.replace(tzinfo=UTC)
            age_s = (anchor - anchor_dt).total_seconds()
            if age_s <= threshold_s:
                continue
            outcome = close_frontier_registry_row(
                dispatch_id,
                "failed",
                "frontier_registry_ttl",
                ledger=led,
            )
            if outcome.get("ok") and outcome.get("marked"):
                reaped.append(dispatch_id)
        except Exception as exc:  # noqa: BLE001 — per-row isolation
            logger.warning(
                "frontier registry reaper row error dispatch_id=%s err=%s",
                dispatch_id,
                exc,
            )
    return reaped


def record_dispatch_registry_best_effort(**kwargs: Any) -> str | None:
    """Never raises — registry write failure must not fail admit."""
    try:
        dispatch_id = kwargs.get("dispatch_id")
        if not dispatch_id:
            return None
        led = kwargs.get("ledger") or CursorDispatchLedger.instance()
        fp = str(dispatch_id)
        with led._connect() as conn:
            settled = conn.execute(
                "SELECT dispatch_id FROM cursor_sdk_dispatches "
                "WHERE (dispatch_id=? OR execution_id=? OR dispatch_id=?) "
                "AND status IN ('completed','failed','cancelled')",
                (f"api-{fp}", fp, fp),
            ).fetchone()
            if settled is not None:
                return str(settled["dispatch_id"])
            exists = conn.execute(
                "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id=?",
                (fp,),
            ).fetchone()
            if (
                exists is not None
                and str(exists["status"]) in _FRONTIER_TERMINAL_STATUSES
            ):
                return fp
        if exists is not None:
            return stamp_registry_on_dispatch_row(
                dispatch_id=str(dispatch_id),
                caller_agent=kwargs.get("caller_agent"),
                seat_name=kwargs.get("seat_name"),
                address=kwargs.get("address"),
                work_key=kwargs.get("work_key"),
                ledger=led,
            )
        return upsert_frontier_registry_dispatch(
            dispatch_id=str(dispatch_id),
            thread_id=str(kwargs.get("thread_id") or ""),
            caller_agent=kwargs.get("caller_agent"),
            seat_name=kwargs.get("seat_name"),
            address=kwargs.get("address") or {},
            work_key=kwargs.get("work_key"),
            resolved_model=str(kwargs.get("resolved_model") or "none"),
            contract=kwargs.get("contract"),
            max_wall_s=kwargs.get("max_wall_s"),
            prompt_bytes=kwargs.get("prompt_bytes"),
            ledger=led,
        )
    except Exception as exc:  # noqa: BLE001 — admit must not fail
        logger.warning(
            "work registry auto-record failed dispatch_id=%s err=%s",
            kwargs.get("dispatch_id"),
            exc,
        )
        return None


def stamp_registry_terminal_work_status(
    *,
    dispatch_id: str,
    terminal_status: str,
    ledger: CursorDispatchLedger | None = None,
    work_status_override: str | None = None,
) -> None:
    """Map terminal dispatch status to registry work_status at closeout."""
    if terminal_status not in ("completed", "failed", "cancelled"):
        return
    if work_status_override is not None:
        work_status = work_status_override
    else:
        work_status = "landed" if terminal_status == "completed" else "abandoned"
    led = ledger or CursorDispatchLedger.instance()
    try:
        with led._connect() as conn:
            row = conn.execute(
                "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
                (dispatch_id,),
            ).fetchone()
            if row is None:
                return
            try:
                rec = json.loads(row["record_json"] or "{}")
            except json.JSONDecodeError:
                rec = {}
            if not isinstance(rec, dict):
                rec = {}
            if not rec.get("registry_auto_recorded"):
                return
            rec["work_status"] = work_status
            conn.execute(
                "UPDATE cursor_sdk_dispatches SET record_json=? WHERE dispatch_id=?",
                (
                    json.dumps(rec, sort_keys=True, separators=(",", ":")),
                    dispatch_id,
                ),
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "registry terminal work_status stamp failed id=%s err=%s",
            dispatch_id,
            exc,
        )

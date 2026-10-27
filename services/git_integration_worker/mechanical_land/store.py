"""Mechanical-land durable run store — sqlite journal + cortex projection."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from durable_io.atomic import durable_write_text
from implement_admission.closeout_helpers import cortex_files_root
from transport_utils import DEFAULT_STARGATE_URL, make_sync_client
from universal_logging import get_logger

from mechanical_land.fold import fold
from mechanical_land.schema import JournalEntry, JournalForkRefused, UnknownJournalEvent
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    _connect,
)
from services.git_integration_worker.mechanical_land.migrations.migration_007_mechanical_land import (
    ensure_mechanical_land_schema,
)

logger = get_logger(__name__)

_PROJECTION_REL = Path("notes/system/runs/mechanical-land")
_STARGATE_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=15.0, pool=5.0)


class MechanicalLandStoreError(Exception):
    """Refused store operation with a stable reason code."""


@dataclass(frozen=True)
class BirthResult:
    run_id: str
    lane_thread_id: str | None
    dispatch_id: str | None
    execution_id: str | None
    status: str
    admit_ok: bool
    http_status: int
    admit_error_class: str | None = None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _mint_run_id() -> str:
    return f"ml-{uuid.uuid4().hex[:12]}"


def _work_key_from_source_ref(source_ref: str) -> str:
    return source_ref.strip()


def _projection_path(run_id: str) -> Path:
    return cortex_files_root() / _PROJECTION_REL / f"{run_id}.md"


def _write_projection(
    *,
    run_id: str,
    state: dict[str, Any],
    events: list[dict[str, Any]],
) -> None:
    dest = _projection_path(run_id)
    body = (
        f"# mechanical-land run {run_id}\n\n"
        f"## state\n\n```json\n{json.dumps(state, indent=2, sort_keys=True)}\n```\n\n"
        f"## events\n\n```json\n{json.dumps(events, indent=2, sort_keys=True)}\n```\n"
    )
    durable_write_text(dest, body, retain_store_root=cortex_files_root())


def _journal_entries_from_rows(
    run_id: str, rows: list[sqlite3.Row]
) -> list[JournalEntry]:
    out: list[JournalEntry] = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        out.append(
            JournalEntry(
                run_id=run_id,
                seq=int(row["seq"]),
                prev_seq=int(row["prev_seq"]),
                step_id=str(row["step_id"]),
                attempt=int(row["attempt"]),
                flow=str(row["flow"]),
                event=str(row["event"]),
                payload=payload if isinstance(payload, dict) else {},
                source=str(row["source"]),
            )
        )
    return out


def build_implement_admit_body(
    *,
    run_id: str,
    lane_thread_id: str,
    source_ref: str,
    policy: dict[str, Any],
    policy_sha256: str,
    caller_agent: str,
) -> dict[str, Any]:
    """§5.2 implement leg admit body for Stargate team/dispatch."""
    models = policy.get("models") or {}
    implement = models.get("implement") or {}
    model = str(implement.get("model") or "cursor/composer-2.5")
    knobs = implement.get("knobs") or {}
    if not isinstance(knobs, dict):
        knobs = {}
    work_key = _work_key_from_source_ref(source_ref)
    return {
        "op": "generate",
        "seat": "cursor-sdk",
        "lane": "B",
        "work_key": work_key,
        "source_ref": source_ref,
        "dispatch_thread_id": lane_thread_id,
        "contract": "implement",
        "model": model,
        "model_knobs": knobs,
        "from_agent": caller_agent,
        "generation_options": {
            "mechanical_land": {
                "run_id": run_id,
                "step_id": "implement",
                "attempt": 1,
                "policy_sha256": policy_sha256,
            }
        },
    }


def _steer_cancel_dispatch(
    dispatch_id: str,
    *,
    reason: str,
    stargate_url: str | None = None,
) -> None:
    body = {
        "op": "steer",
        "steer": "cancel_discard",
        "dispatch_id": dispatch_id,
        "reason": reason,
        "actor": "mechanical-land-store",
    }
    base = (stargate_url or DEFAULT_STARGATE_URL).rstrip("/")
    try:
        with make_sync_client(base, timeout=_STARGATE_TIMEOUT) as client:
            client.post("/api/v1/team/dispatch", json=body)
    except httpx.HTTPError as exc:
        logger.warning(
            "mechanical-land steer cancel failed dispatch_id=%s: %s",
            dispatch_id,
            exc,
        )


def _record_json_from_dispatch_row(record_json: str | None) -> dict[str, Any]:
    try:
        data = json.loads(record_json or "{}")
    except json.JSONDecodeError:
        data = {}
    return data if isinstance(data, dict) else {}


def lookup_mechanical_leg_dispatch(
    *,
    lane_thread_id: str,
    run_id: str,
    step_id: str,
    attempt: int,
) -> dict[str, Any] | None:
    """Find a ledger dispatch stamped for this mechanical-land leg."""
    if not lane_thread_id:
        return None
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        rows = conn.execute(
            "SELECT dispatch_id, execution_id, record_json, status "
            "FROM cursor_sdk_dispatches WHERE thread_id=? "
            "ORDER BY rowid DESC LIMIT 32",
            (lane_thread_id,),
        ).fetchall()
    for row in rows:
        rec = _record_json_from_dispatch_row(row["record_json"])
        gen = rec.get("generation_options") or {}
        if not isinstance(gen, dict):
            continue
        ml = gen.get("mechanical_land") or {}
        if not isinstance(ml, dict):
            continue
        if str(ml.get("run_id") or "") != run_id:
            continue
        if str(ml.get("step_id") or "") != step_id:
            continue
        if int(ml.get("attempt") or 0) != attempt:
            continue
        return {
            "dispatch_id": str(row["dispatch_id"] or ""),
            "execution_id": str(row["execution_id"] or ""),
            "status": str(row["status"] or ""),
        }
    return None


def cancel_unconfirmed_mechanical_leg_dispatches(
    *,
    lane_thread_id: str,
    run_id: str,
    step_id: str,
    attempt: int,
    reason: str,
) -> None:
    """Cancel in-flight dispatches stamped for this leg when admit outcome is unknown."""
    if not lane_thread_id:
        return
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        rows = conn.execute(
            "SELECT dispatch_id, record_json, status "
            "FROM cursor_sdk_dispatches WHERE thread_id=? "
            "ORDER BY rowid DESC LIMIT 32",
            (lane_thread_id,),
        ).fetchall()
    terminal = frozenset({"completed", "failed", "cancelled", "canceled"})
    for row in rows:
        status = str(row["status"] or "").lower()
        if status in terminal:
            continue
        rec = _record_json_from_dispatch_row(row["record_json"])
        gen = rec.get("generation_options") or {}
        ml = gen.get("mechanical_land") if isinstance(gen, dict) else None
        if not isinstance(ml, dict):
            continue
        if str(ml.get("run_id") or "") != run_id:
            continue
        if str(ml.get("step_id") or "") != step_id:
            continue
        if int(ml.get("attempt") or 0) != attempt:
            continue
        dispatch_id = str(row["dispatch_id"] or "").strip()
        if dispatch_id:
            _steer_cancel_dispatch(dispatch_id, reason=reason)


def post_stargate_team_dispatch(
    body: dict[str, Any],
    *,
    stargate_url: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """POST /api/v1/team/dispatch; return (status_code, payload)."""
    base = (stargate_url or DEFAULT_STARGATE_URL).rstrip("/")
    endpoint = "/api/v1/team/dispatch"
    with make_sync_client(base, timeout=_STARGATE_TIMEOUT) as client:
        resp = client.post(endpoint, json=body)
    try:
        payload = resp.json()
    except ValueError:
        payload = {"error": "non_json_response", "text": resp.text[:300]}
    if not isinstance(payload, dict):
        payload = {"error": "non_object_response"}
    return resp.status_code, payload


def mint_lane_thread(
    *,
    run_id: str,
    slug: str | None = None,
) -> str:
    """Mint lane thread with idempotency_key=run_id."""
    from agent_bus_store.db import create_thread

    detail = create_thread(
        thread_id=None,
        slug=slug or f"mechanical-land-{run_id}",
        summary=f"mechanical-land run {run_id}",
        idempotency_key=run_id,
    )
    if detail is None:
        raise MechanicalLandStoreError("lane_thread_unavailable")
    thread_id = str(detail.get("id") or detail.get("thread_id") or "")
    if not thread_id:
        raise MechanicalLandStoreError("lane_thread_unavailable")
    return thread_id


class MechanicalLandStore:
    """SQLite-backed mechanical-land runs on the dispatch ledger database."""

    def __init__(
        self,
        *,
        db_path: Path | None = None,
        mint_lane_thread_fn: Callable[..., str] | None = None,
        post_admit_fn: Callable[..., tuple[int, dict[str, Any]]] | None = None,
    ) -> None:
        self._db_path = db_path
        self._mint_lane_thread = mint_lane_thread_fn or mint_lane_thread
        self._post_admit = post_admit_fn or post_stargate_team_dispatch

    def _connect(self) -> sqlite3.Connection:
        conn = _connect(self._db_path)
        ensure_mechanical_land_schema(conn)
        conn.commit()
        return conn

    def _set_run_status(self, run_id: str, status: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE mechanical_land_runs SET status=?, updated_at=? WHERE run_id=?",
                (status, _now_iso(), run_id),
            )
            conn.commit()

    def _append_leg_admitted_from_ledger(
        self,
        *,
        run_id: str,
        lane_thread_id: str,
        step_id: str,
        attempt: int,
        prev_seq: int,
        admit_body: dict[str, Any],
        found: dict[str, Any],
        packet_sha256: str = "",
        recovered_from: str,
    ) -> tuple[str | None, str | None]:
        dispatch_id = str(found.get("dispatch_id") or "").strip() or None
        execution_id = str(found.get("execution_id") or "").strip() or None
        model = str(admit_body.get("model") or "")
        self.append_event(
            run_id=run_id,
            prev_seq=prev_seq,
            step_id=step_id,
            attempt=attempt,
            flow="main",
            event="leg_admitted",
            payload={
                "step_id": step_id,
                "attempt": attempt,
                "dispatch_id": dispatch_id or "",
                "execution_id": execution_id or "",
                "model": model,
                "contract": str(admit_body.get("contract") or ""),
                "lane": str(admit_body.get("lane") or "B"),
                "sdk_mode": admit_body.get("sdk_mode"),
                "packet_sha256": packet_sha256,
                "recovered_from_transport_error": recovered_from,
            },
            source="reactor",
        )
        self._set_run_status(run_id, "awaiting_leg")
        return dispatch_id, execution_id

    def needs_ledger_recover_before_admit(
        self,
        run_id: str,
        *,
        step_id: str,
        attempt: int,
    ) -> bool:
        """True when a prior ambiguous timeout still owes a ledger confirm before re-POST."""
        snap = self.get_run(run_id, event_limit=200)
        if snap is None:
            return False
        last_failed: dict[str, Any] | None = None
        for ev in snap["events"]:
            if ev.get("event") != "leg_admit_failed":
                continue
            if str(ev.get("step_id") or "") != step_id:
                continue
            if int(ev.get("attempt") or 0) != attempt:
                continue
            last_failed = ev.get("payload") or {}
        if not last_failed or not last_failed.get("recheck_ledger_before_admit"):
            return False
        for ev in snap["events"]:
            if ev.get("event") != "leg_admitted":
                continue
            if str(ev.get("step_id") or "") != step_id:
                continue
            if int(ev.get("attempt") or 0) != attempt:
                continue
            payload = ev.get("payload") or {}
            if str(payload.get("dispatch_id") or "").strip():
                return False
        return True

    def try_recover_ambiguous_admit_before_post(
        self,
        *,
        run_id: str,
        lane_thread_id: str,
        step_id: str,
        attempt: int,
        prev_seq: int,
        admit_body: dict[str, Any],
        packet_sha256: str = "",
    ) -> bool:
        if not self.needs_ledger_recover_before_admit(
            run_id, step_id=step_id, attempt=attempt
        ):
            return False
        found = lookup_mechanical_leg_dispatch(
            lane_thread_id=lane_thread_id,
            run_id=run_id,
            step_id=step_id,
            attempt=attempt,
        )
        if not found:
            return False
        self._append_leg_admitted_from_ledger(
            run_id=run_id,
            lane_thread_id=lane_thread_id,
            step_id=step_id,
            attempt=attempt,
            prev_seq=prev_seq,
            admit_body=admit_body,
            found=found,
            packet_sha256=packet_sha256,
            recovered_from="ledger_recheck",
        )
        return True

    def append_leg_admit_failed(
        self,
        *,
        run_id: str,
        prev_seq: int,
        step_id: str,
        attempt: int,
        error_class: str,
        recheck_ledger_before_admit: bool = False,
        status_code: int = 0,
    ) -> None:
        payload: dict[str, Any] = {
            "step_id": step_id,
            "attempt": attempt,
            "status_code": status_code,
            "error": error_class,
        }
        if recheck_ledger_before_admit:
            payload["recheck_ledger_before_admit"] = True
            payload["transport_ambiguous"] = True
        self.append_event(
            run_id=run_id,
            prev_seq=prev_seq,
            step_id=step_id,
            attempt=attempt,
            flow="main",
            event="leg_admit_failed",
            payload=payload,
            source="reactor",
        )

    def recover_or_fail_admit_transport_error(
        self,
        *,
        run_id: str,
        lane_thread_id: str,
        step_id: str,
        attempt: int,
        prev_seq: int,
        admit_body: dict[str, Any],
        exc: httpx.HTTPError,
        packet_sha256: str = "",
    ) -> tuple[bool, str | None, str | None]:
        """After admit POST transport failure: ledger lookup when ambiguous, else fail."""
        error_class = type(exc).__name__
        connect_failure = isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))
        ambiguous = isinstance(exc, httpx.ReadTimeout)
        if connect_failure:
            self.append_leg_admit_failed(
                run_id=run_id,
                prev_seq=prev_seq,
                step_id=step_id,
                attempt=attempt,
                error_class=error_class,
            )
            return False, None, None
        found = lookup_mechanical_leg_dispatch(
            lane_thread_id=lane_thread_id,
            run_id=run_id,
            step_id=step_id,
            attempt=attempt,
        )
        if found:
            dispatch_id, execution_id = self._append_leg_admitted_from_ledger(
                run_id=run_id,
                lane_thread_id=lane_thread_id,
                step_id=step_id,
                attempt=attempt,
                prev_seq=prev_seq,
                admit_body=admit_body,
                found=found,
                packet_sha256=packet_sha256,
                recovered_from=error_class,
            )
            return True, dispatch_id, execution_id
        cancel_unconfirmed_mechanical_leg_dispatches(
            lane_thread_id=lane_thread_id,
            run_id=run_id,
            step_id=step_id,
            attempt=attempt,
            reason=f"admit_transport_error:{error_class}",
        )
        self.append_leg_admit_failed(
            run_id=run_id,
            prev_seq=prev_seq,
            step_id=step_id,
            attempt=attempt,
            error_class=error_class,
            recheck_ledger_before_admit=ambiguous,
        )
        return False, None, None

    def _recover_or_block_admit_transport_error(
        self,
        *,
        run_id: str,
        lane_thread_id: str,
        step_id: str,
        attempt: int,
        prev_seq: int,
        admit_body: dict[str, Any],
        exc: httpx.HTTPError,
    ) -> BirthResult | None:
        recovered, dispatch_id, execution_id = (
            self.recover_or_fail_admit_transport_error(
                run_id=run_id,
                lane_thread_id=lane_thread_id,
                step_id=step_id,
                attempt=attempt,
                prev_seq=prev_seq,
                admit_body=admit_body,
                exc=exc,
            )
        )
        if recovered:
            return BirthResult(
                run_id=run_id,
                lane_thread_id=lane_thread_id,
                dispatch_id=dispatch_id,
                execution_id=execution_id,
                status="awaiting_leg",
                admit_ok=True,
                http_status=201,
            )
        self._set_run_status(run_id, "blocked")
        return None

    def find_run_by_idempotency_key(self, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT run_id FROM mechanical_land_runs WHERE idempotency_key=?",
                (key,),
            ).fetchone()
        return str(row["run_id"]) if row else None

    def append_event(
        self,
        *,
        run_id: str,
        prev_seq: int,
        step_id: str,
        attempt: int,
        flow: str,
        event: str,
        payload: dict[str, Any],
        source: str,
    ) -> int:
        """CAS append; returns new seq. Raises JournalForkRefused on fork."""
        ts = _now_iso()
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS tip FROM mechanical_land_journal "
                "WHERE run_id=?",
                (run_id,),
            ).fetchone()
            tip = int(row["tip"]) if row else 0
            if prev_seq != tip:
                conn.execute("ROLLBACK")
                raise JournalForkRefused(
                    f"prev_seq {prev_seq} != journal tip {tip} for {run_id}"
                )
            new_seq = tip + 1
            conn.execute(
                "INSERT INTO mechanical_land_journal "
                "(run_id, seq, prev_seq, step_id, attempt, flow, event, "
                "payload_json, source, written_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    new_seq,
                    prev_seq,
                    step_id,
                    attempt,
                    flow,
                    event,
                    payload_json,
                    source,
                    ts,
                ),
            )
            conn.execute(
                "UPDATE mechanical_land_runs SET updated_at=? WHERE run_id=?",
                (ts, run_id),
            )
            conn.commit()
        try:
            self.refresh_projection(run_id)
        except UnknownJournalEvent as exc:
            logger.warning(
                "mechanical-land projection fold skipped run_id=%s after append: %s",
                run_id,
                exc,
            )
        return new_seq

    def refresh_projection(self, run_id: str) -> None:
        try:
            snapshot = self.get_run(run_id, event_limit=20)
        except UnknownJournalEvent as exc:
            logger.warning(
                "mechanical-land projection fold skipped run_id=%s: %s",
                run_id,
                exc,
            )
            return
        if snapshot is None:
            return
        _write_projection(
            run_id=run_id,
            state=snapshot["state"],
            events=snapshot["events"],
        )

    def _snapshot_from_rows(
        self,
        run_row: sqlite3.Row,
        rows_chrono: list[sqlite3.Row],
        *,
        event_limit: int,
    ) -> dict[str, Any]:
        run_id = str(run_row["run_id"])
        entries = _journal_entries_from_rows(run_id, rows_chrono)
        folded = fold(entries)
        folded.run_id = run_id
        folded.kind = str(run_row["kind"])
        folded.source_ref = str(run_row["source_ref"])
        state = folded.result_json()
        state["status"] = str(run_row["status"])
        state["lane_thread_id"] = run_row["lane_thread_id"]
        state["dispatch_thread_id"] = run_row["dispatch_thread_id"]
        events_out = [
            {
                "seq": int(r["seq"]),
                "step_id": r["step_id"],
                "attempt": int(r["attempt"]),
                "flow": r["flow"],
                "event": r["event"],
                "payload": json.loads(r["payload_json"] or "{}"),
                "source": r["source"],
                "written_at": r["written_at"],
            }
            for r in rows_chrono
        ]
        tail = (
            events_out[-event_limit:] if len(events_out) > event_limit else events_out
        )
        return {
            "run_id": run_id,
            "state": state,
            "events": tail,
        }

    def get_run(self, run_id: str, *, event_limit: int = 20) -> dict[str, Any] | None:
        with self._connect() as conn:
            run_row = conn.execute(
                "SELECT * FROM mechanical_land_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if run_row is None:
                return None
            rows = conn.execute(
                "SELECT * FROM mechanical_land_journal WHERE run_id=? ORDER BY seq ASC",
                (run_id,),
            ).fetchall()
        return self._snapshot_from_rows(run_row, list(rows), event_limit=event_limit)

    def birth_from_plan(
        self,
        plan: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> BirthResult:
        """Create run, mint lane thread, admit first implement leg (§13 stage 3)."""
        if plan.get("admitted") is False:
            raise MechanicalLandStoreError("plan_not_admitted")
        kind = str(plan.get("kind") or "implement")
        source_ref = str(plan.get("source_ref") or "")
        if not source_ref:
            raise MechanicalLandStoreError("missing_source_ref")
        caller_agent = str(plan.get("caller_agent") or "mechanical-land")
        dispatch_thread_id = str(plan.get("dispatch_thread_id") or "")
        if not dispatch_thread_id:
            raise MechanicalLandStoreError("missing_dispatch_thread_id")
        policy = plan.get("policy") or {}
        if not isinstance(policy, dict):
            policy = {}
        policy_sha256 = str(plan.get("policy_sha256") or "")
        if idempotency_key:
            existing = self.find_run_by_idempotency_key(idempotency_key)
            if existing:
                snap = self.get_run(existing)
                assert snap is not None
                return BirthResult(
                    run_id=existing,
                    lane_thread_id=snap["state"].get("lane_thread_id"),
                    dispatch_id=None,
                    execution_id=None,
                    status=str(snap["state"].get("status") or "running"),
                    admit_ok=True,
                    http_status=200,
                )

        run_id = _mint_run_id()
        ts = _now_iso()
        work_key = _work_key_from_source_ref(source_ref)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO mechanical_land_runs "
                "(run_id, kind, source_ref, work_key, caller_agent, "
                "dispatch_thread_id, lane_thread_id, policy_json, policy_sha256, "
                "status, current_step, current_attempt, flow, result_json, "
                "idempotency_key, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, 'birthing', 'run', 1, "
                "'main', NULL, ?, ?, ?)",
                (
                    run_id,
                    kind,
                    source_ref,
                    work_key,
                    caller_agent,
                    dispatch_thread_id,
                    json.dumps(policy, sort_keys=True),
                    policy_sha256,
                    idempotency_key,
                    ts,
                    ts,
                ),
            )
            conn.commit()

        seq = 0
        seq = self.append_event(
            run_id=run_id,
            prev_seq=seq,
            step_id="run",
            attempt=1,
            flow="main",
            event="run_birthed",
            payload={
                "kind": kind,
                "source_ref": source_ref,
                "policy_sha256": policy_sha256,
                "caller": caller_agent,
            },
            source="caller",
        )
        seq = self.append_event(
            run_id=run_id,
            prev_seq=seq,
            step_id="run",
            attempt=1,
            flow="main",
            event="policy_stamped",
            payload={"policy": policy},
            source="caller",
        )

        try:
            lane_thread_id = self._mint_lane_thread(run_id=run_id)
        except MechanicalLandStoreError:
            self._set_run_status(run_id, "blocked")
            raise
        except Exception as exc:
            logger.warning("lane thread mint failed run_id=%s: %s", run_id, exc)
            self._set_run_status(run_id, "blocked")
            raise MechanicalLandStoreError("lane_thread_unavailable") from exc

        with self._connect() as conn:
            conn.execute(
                "UPDATE mechanical_land_runs SET lane_thread_id=?, status='awaiting_leg', "
                "updated_at=? WHERE run_id=?",
                (lane_thread_id, _now_iso(), run_id),
            )
            conn.commit()

        admit_body = build_implement_admit_body(
            run_id=run_id,
            lane_thread_id=lane_thread_id,
            source_ref=source_ref,
            policy=policy,
            policy_sha256=policy_sha256,
            caller_agent=caller_agent,
        )
        try:
            status_code, payload = self._post_admit(admit_body)
        except Exception as exc:
            if isinstance(exc, httpx.HTTPError):
                recovered = self._recover_or_block_admit_transport_error(
                    run_id=run_id,
                    lane_thread_id=lane_thread_id,
                    step_id="implement",
                    attempt=1,
                    prev_seq=seq,
                    admit_body=admit_body,
                    exc=exc,
                )
                if recovered is not None:
                    return recovered
                error_class = type(exc).__name__
            else:
                self.append_leg_admit_failed(
                    run_id=run_id,
                    prev_seq=seq,
                    step_id="implement",
                    attempt=1,
                    error_class=type(exc).__name__,
                )
                error_class = type(exc).__name__
            self._set_run_status(run_id, "blocked")
            return BirthResult(
                run_id=run_id,
                lane_thread_id=lane_thread_id,
                dispatch_id=None,
                execution_id=None,
                status="blocked",
                admit_ok=False,
                http_status=503,
                admit_error_class=error_class,
            )
        if status_code >= 400:
            self.append_event(
                run_id=run_id,
                prev_seq=seq,
                step_id="implement",
                attempt=1,
                flow="main",
                event="leg_admit_failed",
                payload={
                    "status_code": status_code,
                    "error": json.dumps(payload)[:2000],
                },
                source="reactor",
            )
            self._set_run_status(run_id, "blocked")
            return BirthResult(
                run_id=run_id,
                lane_thread_id=lane_thread_id,
                dispatch_id=None,
                execution_id=None,
                status="blocked",
                admit_ok=False,
                http_status=502,
            )

        dispatch_id = (
            str(payload.get("dispatch_id") or payload.get("execution_id") or "").strip()
            or None
        )
        execution_id = str(payload.get("execution_id") or "").strip() or None
        model = str(admit_body.get("model") or "")
        seq = self.append_event(
            run_id=run_id,
            prev_seq=seq,
            step_id="implement",
            attempt=1,
            flow="main",
            event="leg_admitted",
            payload={
                "step_id": "implement",
                "attempt": 1,
                "dispatch_id": dispatch_id or "",
                "execution_id": execution_id or "",
                "model": model,
                "contract": "implement",
                "lane": "B",
                "sdk_mode": None,
                "packet_sha256": "",
            },
            source="reactor",
        )
        with self._connect() as conn:
            conn.execute(
                "UPDATE mechanical_land_runs SET status='awaiting_leg', "
                "current_step='implement', current_attempt=1, updated_at=? "
                "WHERE run_id=?",
                (_now_iso(), run_id),
            )
            conn.commit()

        return BirthResult(
            run_id=run_id,
            lane_thread_id=lane_thread_id,
            dispatch_id=dispatch_id,
            execution_id=execution_id,
            status="awaiting_leg",
            admit_ok=True,
            http_status=201,
        )

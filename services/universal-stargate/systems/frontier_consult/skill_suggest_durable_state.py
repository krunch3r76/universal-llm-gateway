"""Durable catch-up for skill-suggest worker completion (Event Service + ledger)."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from transport_utils import EVENTS_QUERY_SOCK, make_sync_client

TerminalSignal = Literal[
    "frontier.sdk.worker.completed",
    "frontier.sdk.worker.failed",
    "frontier.sdk.worker.delivery_failed",
    "frontier.sdk.worker.timeout",
]

TERMINAL_SIGNALS: tuple[TerminalSignal, ...] = (
    "frontier.sdk.worker.completed",
    "frontier.sdk.worker.failed",
    "frontier.sdk.worker.delivery_failed",
    "frontier.sdk.worker.timeout",
)

_EVENTS_QUERY_URL = f"unix://{EVENTS_QUERY_SOCK}"


@dataclass(frozen=True, slots=True)
class DurableTerminalEvent:
    signal: TerminalSignal
    dispatch_id: str | None
    thread_id: str | None
    execution_id: str | None
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class LedgerDispatchRow:
    dispatch_id: str
    thread_id: str
    execution_id: str | None
    status: str
    terminal_status: str | None
    last_heartbeat_at: str | None
    started_at: str | None
    queued_at: str | None


def _ledger_path() -> Path:
    data_dir = Path(os.getenv("DATA_DIR", str(Path.home() / ".gateway"))).expanduser()
    return data_dir / "cursor-sdk-dispatch.db"


def _parse_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def query_event_service(operation: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        with make_sync_client(_EVENTS_QUERY_URL, timeout=5.0) as client:
            resp = client.post(
                "/v1/query",
                json={"type": "operation", "name": operation, "params": params},
            )
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        return {}
    return {}


def _event_matches(
    row: dict[str, Any],
    *,
    execution_id: str,
    thread_id: str,
    dispatch_id: str | None,
) -> bool:
    payload = _parse_payload(row.get("payload"))
    row_exec = str(row.get("execution_id") or payload.get("execution_id") or "")
    row_thread = str(payload.get("thread_id") or "")
    if row_exec != execution_id or row_thread != thread_id:
        return False
    if dispatch_id:
        row_dispatch = str(payload.get("dispatch_id") or "")
        if row_dispatch and row_dispatch != dispatch_id:
            return False
    return True


def find_durable_terminal_event(
    *,
    execution_id: str,
    thread_id: str,
    dispatch_id: str | None,
) -> DurableTerminalEvent | None:
    for signal in TERMINAL_SIGNALS:
        result = query_event_service(
            "signal-events",
            {"signal": signal, "execution_id": execution_id, "limit": 20},
        )
        for row in result.get("rows") or []:
            if not isinstance(row, dict):
                continue
            if not _event_matches(
                row,
                execution_id=execution_id,
                thread_id=thread_id,
                dispatch_id=dispatch_id,
            ):
                continue
            payload = _parse_payload(row.get("payload"))
            return DurableTerminalEvent(
                signal=signal,
                dispatch_id=str(payload.get("dispatch_id") or "") or None,
                thread_id=str(payload.get("thread_id") or "") or None,
                execution_id=str(payload.get("execution_id") or "") or None,
                payload=payload,
            )
    return None


def read_ledger_dispatch_row(
    *,
    dispatch_id: str | None = None,
    execution_id: str | None = None,
    thread_id: str | None = None,
) -> LedgerDispatchRow | None:
    path = _ledger_path()
    if not path.is_file():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        if dispatch_id:
            row = conn.execute(
                "SELECT dispatch_id, thread_id, execution_id, status, terminal_status, "
                "last_heartbeat_at, started_at, queued_at "
                "FROM cursor_sdk_dispatches WHERE dispatch_id=?",
                (dispatch_id,),
            ).fetchone()
        elif execution_id:
            row = conn.execute(
                "SELECT dispatch_id, thread_id, execution_id, status, terminal_status, "
                "last_heartbeat_at, started_at, queued_at "
                "FROM cursor_sdk_dispatches WHERE execution_id=? "
                "ORDER BY rowid DESC LIMIT 1",
                (execution_id,),
            ).fetchone()
        elif thread_id:
            row = conn.execute(
                "SELECT dispatch_id, thread_id, execution_id, status, terminal_status, "
                "last_heartbeat_at, started_at, queued_at "
                "FROM cursor_sdk_dispatches WHERE thread_id=? "
                "ORDER BY rowid DESC LIMIT 1",
                (thread_id,),
            ).fetchone()
        else:
            return None
    finally:
        conn.close()
    if row is None:
        return None
    return LedgerDispatchRow(
        dispatch_id=row["dispatch_id"],
        thread_id=row["thread_id"],
        execution_id=row["execution_id"],
        status=row["status"],
        terminal_status=row["terminal_status"],
        last_heartbeat_at=row["last_heartbeat_at"],
        started_at=row["started_at"],
        queued_at=row["queued_at"],
    )


def ledger_has_terminal(ledger: LedgerDispatchRow | None) -> bool:
    if ledger is None:
        return False
    if ledger.terminal_status in {"completed", "failed"}:
        return True
    return ledger.status in {"completed", "failed"}


def durable_idle_seconds(ledger: LedgerDispatchRow | None) -> float | None:
    if ledger is None:
        return None
    ts = ledger.last_heartbeat_at or ledger.started_at or ledger.queued_at
    if not ts:
        return None
    try:
        seen = datetime.fromisoformat(ts).timestamp()
    except ValueError:
        return None
    return max(0.0, datetime.now(UTC).timestamp() - seen)


def infer_terminal_from_ledger(
    ledger: LedgerDispatchRow | None,
) -> DurableTerminalEvent | None:
    if not ledger_has_terminal(ledger) or ledger is None:
        return None
    if ledger.terminal_status == "failed" or ledger.status == "failed":
        signal: TerminalSignal = "frontier.sdk.worker.failed"
    else:
        signal = "frontier.sdk.worker.completed"
    return DurableTerminalEvent(
        signal=signal,
        dispatch_id=ledger.dispatch_id,
        thread_id=ledger.thread_id,
        execution_id=ledger.execution_id,
        payload={
            "dispatch_id": ledger.dispatch_id,
            "thread_id": ledger.thread_id,
            "execution_id": ledger.execution_id,
            "terminal_status": ledger.terminal_status or ledger.status,
        },
    )


def durable_catch_up_terminal(
    *,
    execution_id: str,
    thread_id: str,
    dispatch_id: str | None,
) -> DurableTerminalEvent | None:
    terminal = find_durable_terminal_event(
        execution_id=execution_id,
        thread_id=thread_id,
        dispatch_id=dispatch_id,
    )
    if terminal is not None:
        return terminal
    ledger = read_ledger_dispatch_row(
        dispatch_id=dispatch_id,
        execution_id=execution_id,
        thread_id=thread_id,
    )
    return infer_terminal_from_ledger(ledger)

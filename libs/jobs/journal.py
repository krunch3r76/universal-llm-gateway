"""Append-only SQLite journal for jobs runs. The fold is the authority.

Delivery rows share the transitions table and never change the run status.
Restart reconcile appends ``lost`` for every fold still in admitted,
running, or cancelling. Callers are the HTTP app, the runner, and the
busy probe (which reads the same file directly).
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jobs import events

_TERMINAL = frozenset({"completed", "failed", "cancelled", "lost"})
_OPEN = frozenset({"admitted", "running", "cancelling"})
DELIVERY_ATTEMPT_CAP = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    job TEXT NOT NULL,
    args_json TEXT NOT NULL,
    surface TEXT NOT NULL,
    output_contract TEXT NOT NULL,
    target_thread TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS transitions (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    state TEXT NOT NULL,
    at TEXT NOT NULL,
    data_json TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);
"""


def state_dir() -> Path:
    """Return JOBS_STATE_DIR or ``~/.local/share/ulg-jobs``."""
    raw = os.environ.get("JOBS_STATE_DIR", "")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".local" / "share" / "ulg-jobs"


def journal_path() -> Path:
    """Return the SQLite path the satellite and the busy probe share."""
    return state_dir() / "journal.db"


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class Fold:
    """Latest non-delivery transition plus the immutable run row."""

    run_id: str
    job: str
    state: str
    data: dict[str, Any]
    created_at: str
    updated_at: str
    surface: str
    output_contract: str
    target_thread: str | None
    args: dict[str, Any]
    seq: int


class Journal:
    """WAL journal. ``append`` commits, then emits exactly one jobs.run event."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or journal_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        (self.path.parent / "runs").mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._listeners: list[Any] = []
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.execute("PRAGMA journal_mode=WAL")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def add_listener(self, fn: Any) -> None:
        """Register a callback invoked after each committed append."""
        self._listeners.append(fn)

    def log_path(self, run_id: str) -> Path:
        """Return the combined stdout/stderr log for one run."""
        return self.path.parent / "runs" / f"{run_id}.log"

    def admit(
        self,
        *,
        run_id: str,
        job: str,
        args: dict[str, Any],
        surface: str,
        output_contract: str,
        target_thread: str | None,
    ) -> Fold:
        """Insert the run row and the admitted transition in one transaction."""
        created = _now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runs (
                    run_id, job, args_json, surface, output_contract,
                    target_thread, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    job,
                    json.dumps(args),
                    surface,
                    output_contract,
                    target_thread,
                    created,
                ),
            )
            conn.execute(
                """
                INSERT INTO transitions (run_id, seq, state, at, data_json)
                VALUES (?, 1, 'admitted', ?, '{}')
                """,
                (run_id, created),
            )
        self._after("admitted", run_id, {})
        fold = self.fold(run_id)
        assert fold is not None
        return fold

    def append(self, run_id: str, state: str, data: dict[str, Any] | None = None) -> Fold:
        """Append one transition and emit its event after the commit."""
        payload = data or {}
        at = _now()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM transitions WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            seq = int(row[0])
            conn.execute(
                """
                INSERT INTO transitions (run_id, seq, state, at, data_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, seq, state, at, json.dumps(payload)),
            )
        self._after(state, run_id, payload)
        fold = self.fold(run_id)
        assert fold is not None
        return fold

    def _after(self, state: str, run_id: str, data: dict[str, Any]) -> None:
        fold = self.fold(run_id)
        if fold is None:
            return
        events.emit_transition(
            state,
            run_id=run_id,
            job=fold.job,
            surface=fold.surface,
            output_contract=fold.output_contract,
            data=data,
            target_thread=fold.target_thread,
        )
        for fn in self._listeners:
            fn(run_id)

    def fold(self, run_id: str) -> Fold | None:
        """Return the latest non-delivery transition, or None when unknown."""
        with self._connect() as conn:
            run = conn.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run is None:
                return None
            row = conn.execute(
                """
                SELECT seq, state, at, data_json FROM transitions
                WHERE run_id = ? AND state NOT IN ('delivered', 'undelivered')
                ORDER BY seq DESC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return Fold(
            run_id=run_id,
            job=run["job"],
            state=row["state"],
            data=json.loads(row["data_json"]),
            created_at=run["created_at"],
            updated_at=row["at"],
            surface=run["surface"],
            output_contract=run["output_contract"],
            target_thread=run["target_thread"],
            args=json.loads(run["args_json"]),
            seq=int(row["seq"]),
        )

    def latest_delivery(self, run_id: str) -> tuple[str, dict[str, Any]] | None:
        """Return the newest delivery row state and payload, if any."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT state, data_json FROM transitions
                WHERE run_id = ? AND state IN ('delivered', 'undelivered')
                ORDER BY seq DESC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return row["state"], json.loads(row["data_json"])

    def delivery_attempts(self, run_id: str) -> int:
        """Count delivery rows. The cap is ``DELIVERY_ATTEMPT_CAP``."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) FROM transitions
                WHERE run_id = ? AND state IN ('delivered', 'undelivered')
                """,
                (run_id,),
            ).fetchone()
        return int(row[0])

    def pending_deliveries(self) -> list[str]:
        """Terminal thread runs whose latest delivery is missing or undelivered.

        Stops at the journaled attempt cap so a 503 is retried once and not
        forever. A delivered row removes the run from this list.
        """
        pending: list[str] = []
        with self._connect() as conn:
            runs = conn.execute(
                """
                SELECT run_id FROM runs WHERE output_contract = 'thread'
                """
            ).fetchall()
        for row in runs:
            run_id = row["run_id"]
            fold = self.fold(run_id)
            if fold is None or fold.state not in _TERMINAL:
                continue
            if self.delivery_attempts(run_id) >= DELIVERY_ATTEMPT_CAP:
                continue
            latest = self.latest_delivery(run_id)
            if latest is None or latest[0] == "undelivered":
                pending.append(run_id)
        return pending

    def reconcile_on_start(self) -> list[str]:
        """Kill live process groups for open folds, then append lost.

        A run never reads running after this returns. ``pgid`` with no live
        members is still marked lost. Returns the run ids that were closed.
        """
        import signal

        lost: list[str] = []
        with self._connect() as conn:
            ids = [row["run_id"] for row in conn.execute("SELECT run_id FROM runs")]
        for run_id in ids:
            fold = self.fold(run_id)
            if fold is None or fold.state not in _OPEN:
                continue
            pgid = fold.data.get("pgid")
            if isinstance(pgid, int):
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    pass
            self.append(
                run_id,
                "lost",
                {"recovery": "restart_reconcile"},
            )
            lost.append(run_id)
        return lost

    def open_runs(self) -> list[dict[str, Any]]:
        """Return open folds for the busy probe. Reads this journal file."""
        found: list[dict[str, Any]] = []
        with self._connect() as conn:
            ids = [row["run_id"] for row in conn.execute("SELECT run_id FROM runs")]
        for run_id in ids:
            fold = self.fold(run_id)
            if fold is not None and fold.state in _OPEN:
                found.append(
                    {"run_id": run_id, "job": fold.job, "state": fold.state}
                )
        return found

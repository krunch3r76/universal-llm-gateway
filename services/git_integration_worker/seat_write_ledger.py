"""Durable write ledger for lane-B IDE / attended model seats.

Non-dispatch model writes register here at edit time so a quiescent sweeper
can commit from record rather than inferring authorship from the tree.
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from universal_logging import get_logger

logger = get_logger(__name__)

_STATUS_OPEN = "open"
_STATUS_CLOSED = "closed"

_DDL = """
CREATE TABLE IF NOT EXISTS seat_write_arcs (
    arc_id       TEXT PRIMARY KEY,
    seat_id      TEXT NOT NULL,
    source_repo  TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('open','closed')),
    opened_at    TEXT NOT NULL,
    closed_at    TEXT
);
CREATE TABLE IF NOT EXISTS seat_write_paths (
    arc_id       TEXT NOT NULL,
    source_repo  TEXT NOT NULL,
    path         TEXT NOT NULL,
    seat_id      TEXT NOT NULL,
    registered_at TEXT NOT NULL,
    last_touch_at TEXT NOT NULL,
    PRIMARY KEY (arc_id, path)
);
CREATE INDEX IF NOT EXISTS idx_seat_write_paths_repo
    ON seat_write_paths(source_repo);
CREATE INDEX IF NOT EXISTS idx_seat_write_arcs_repo_status
    ON seat_write_arcs(source_repo, status);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _ledger_path() -> Path:
    data_dir = Path(os.getenv("DATA_DIR", str(Path.home() / ".gateway"))).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "seat-write-ledger.db"


def _connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = path or _ledger_path()
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@dataclass(frozen=True, slots=True)
class QuiescentArcBatch:
    """Registered paths on one closed arc eligible for sweep."""

    arc_id: str
    seat_id: str
    paths: tuple[str, ...]


class SeatWriteLedger:
    """Singleton ledger for attended-seat write registration."""

    _instance: SeatWriteLedger | None = None

    def __init__(self, *, db_path: Path | None = None) -> None:
        self._db_path = db_path or _ledger_path()
        with self._connect() as conn:
            conn.executescript(_DDL)

    def _connect(self) -> sqlite3.Connection:
        return _connect(self._db_path)

    @classmethod
    def instance(cls) -> SeatWriteLedger:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    def open_arc(
        self,
        *,
        arc_id: str,
        seat_id: str,
        source_repo: str,
    ) -> None:
        repo = str(Path(source_repo).resolve())
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO seat_write_arcs (arc_id, seat_id, source_repo, status, opened_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(arc_id) DO UPDATE SET "
                "seat_id=excluded.seat_id, source_repo=excluded.source_repo, "
                "status=?, closed_at=NULL",
                (arc_id, seat_id, repo, _STATUS_OPEN, _now(), _STATUS_OPEN),
            )

    def close_arc(self, *, arc_id: str) -> bool:
        with self._connect() as conn:
            updated = conn.execute(
                "UPDATE seat_write_arcs SET status=?, closed_at=? "
                "WHERE arc_id=? AND status=?",
                (_STATUS_CLOSED, _now(), arc_id, _STATUS_OPEN),
            )
            return updated.rowcount == 1

    def is_arc_open(self, *, arc_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM seat_write_arcs WHERE arc_id=?",
                (arc_id,),
            ).fetchone()
        return row is not None and row["status"] == _STATUS_OPEN

    def register_paths(
        self,
        *,
        arc_id: str,
        seat_id: str,
        source_repo: str,
        paths: tuple[str, ...] | list[str],
    ) -> int:
        repo = str(Path(source_repo).resolve())
        normalized = tuple(dict.fromkeys(p.strip().lstrip("/") for p in paths if p.strip()))
        if not normalized:
            return 0
        ts = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO seat_write_arcs (arc_id, seat_id, source_repo, status, opened_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(arc_id) DO NOTHING",
                (arc_id, seat_id, repo, _STATUS_OPEN, ts),
            )
            for path in normalized:
                conn.execute(
                    "INSERT INTO seat_write_paths "
                    "(arc_id, source_repo, path, seat_id, registered_at, last_touch_at) "
                    "VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(arc_id, path) DO UPDATE SET "
                    "last_touch_at=excluded.last_touch_at, seat_id=excluded.seat_id",
                    (arc_id, repo, path, seat_id, ts, ts),
                )
        return len(normalized)

    def registered_paths(self, *, source_repo: str) -> frozenset[str]:
        repo = str(Path(source_repo).resolve())
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT path FROM seat_write_paths WHERE source_repo=?",
                (repo,),
            ).fetchall()
        return frozenset(row["path"] for row in rows)

    def quiescent_batches(
        self,
        *,
        source_repo: str,
        quiescence_s: float,
        now_mono: float | None = None,
    ) -> list[QuiescentArcBatch]:
        """Closed arcs whose registered paths have not been touched recently."""
        repo = str(Path(source_repo).resolve())
        cutoff = _now_before(quiescence_s, now_mono=now_mono)
        with self._connect() as conn:
            arcs = conn.execute(
                "SELECT arc_id, seat_id FROM seat_write_arcs "
                "WHERE source_repo=? AND status=?",
                (repo, _STATUS_CLOSED),
            ).fetchall()
            out: list[QuiescentArcBatch] = []
            for arc in arcs:
                rows = conn.execute(
                    "SELECT path FROM seat_write_paths "
                    "WHERE arc_id=? AND source_repo=? AND last_touch_at <= ?",
                    (arc["arc_id"], repo, cutoff),
                ).fetchall()
                if rows:
                    out.append(
                        QuiescentArcBatch(
                            arc_id=arc["arc_id"],
                            seat_id=arc["seat_id"],
                            paths=tuple(row["path"] for row in rows),
                        )
                    )
        return out

    def clear_swept_paths(
        self,
        *,
        arc_id: str,
        paths: tuple[str, ...] | list[str],
    ) -> None:
        if not paths:
            return
        with self._connect() as conn:
            for path in paths:
                conn.execute(
                    "DELETE FROM seat_write_paths WHERE arc_id=? AND path=?",
                    (arc_id, path),
                )

    def open_arcs_with_paths(self, *, source_repo: str) -> tuple[str, ...]:
        repo = str(Path(source_repo).resolve())
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT a.arc_id FROM seat_write_arcs a "
                "JOIN seat_write_paths p ON p.arc_id=a.arc_id "
                "WHERE a.source_repo=? AND a.status=?",
                (repo, _STATUS_OPEN),
            ).fetchall()
        return tuple(row["arc_id"] for row in rows)


def _now_before(seconds: float, *, now_mono: float | None = None) -> str:
    """ISO timestamp at ``now - seconds`` for deterministic tests."""
    if now_mono is not None:
        target = datetime.fromtimestamp(now_mono - seconds, tz=UTC)
        return target.isoformat()
    return datetime.fromtimestamp(time.time() - seconds, tz=UTC).isoformat()


__all__ = [
    "QuiescentArcBatch",
    "SeatWriteLedger",
]

"""SQLite WAL ledger for Anthropic API credit reservations and commits (leg A3)."""

from __future__ import annotations

import contextlib
import os
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY,
    kind TEXT CHECK(kind IN ('reserve','commit','release','latch')),
    request_id TEXT,
    model TEXT,
    usd TEXT,
    created_at TEXT,
    window_start TEXT,
    tag TEXT,
    mcp_class INTEGER
);
"""


class LedgerUnavailable(Exception):  # noqa: N818
    """Raised when the SQLite credit ledger is missing, locked, or corrupt."""


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """Single row from the ``entries`` table."""

    id: int
    kind: str
    request_id: str | None
    model: str | None
    usd: Decimal
    created_at: str
    window_start: str
    tag: str | None
    mcp_class: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "request_id": self.request_id,
            "model": self.model,
            "usd": str(self.usd),
            "created_at": self.created_at,
            "window_start": self.window_start,
            "tag": self.tag,
            "mcp_class": int(self.mcp_class),
        }


@dataclass(frozen=True, slots=True)
class WindowSnapshot:
    """Aggregated ledger state inside a meter window."""

    committed_usd: Decimal
    open_reservations_usd: Decimal
    open_reservation_count: int
    open_mcp_count: int
    spend_since_as_of_usd: Decimal
    latch_active: bool
    latch_reason: str | None
    overrun_count: int


def parse_utc_iso(raw: str) -> datetime:
    """Parse an ISO-8601 timestamp string into a UTC-aware datetime."""
    normalized = str(raw).strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise ValueError(f"Expected tz-aware ISO timestamp, got {raw!r}")
    return dt.astimezone(UTC)


def validate_ledger_path(raw_path: Path | str) -> Path:
    """Expand ``~`` and refuse paths under ``/mnt/`` (K38)."""
    if raw_path is None or not str(raw_path).strip():
        raise LedgerUnavailable("ledger_path is empty")
    expanded = Path(str(raw_path).strip()).expanduser()
    if str(expanded).startswith("/mnt/"):
        raise LedgerUnavailable(f"ledger_path refused under /mnt/ (K38): {expanded}")
    try:
        resolved = expanded.resolve()
    except OSError as exc:
        raise LedgerUnavailable(
            f"cannot resolve ledger_path {expanded}: {exc}"
        ) from exc
    if str(resolved).startswith("/mnt/"):
        raise LedgerUnavailable(f"ledger_path resolves under /mnt/ (K38): {resolved}")
    return resolved


class CreditLedger:
    """Process- and thread-safe SQLite WAL ledger for Anthropic cycle spend."""

    def __init__(
        self,
        ledger_path: Path | str,
        *,
        busy_timeout_ms: int = 1000,
        create_if_missing: bool = True,
    ) -> None:
        self._path = validate_ledger_path(ledger_path)
        self._busy_timeout_ms = max(1, int(busy_timeout_ms))
        self._bootstrapped = False
        if create_if_missing:
            self._bootstrap()
        elif not self._path.is_file():
            raise LedgerUnavailable(f"ledger file missing: {self._path}")

    @property
    def path(self) -> Path:
        return self._path

    def _bootstrap(self) -> None:
        parent = self._path.parent
        try:
            parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(parent, 0o700)
        except OSError as exc:
            raise LedgerUnavailable(
                f"failed to create ledger parent directory {parent}: {exc}"
            ) from exc

        if self._path.exists() and not self._path.is_file():
            raise LedgerUnavailable(f"ledger_path is not a regular file: {self._path}")

        try:
            with contextlib.closing(
                sqlite3.connect(
                    str(self._path),
                    timeout=self._busy_timeout_ms / 1000.0,
                    isolation_level=None,
                )
            ) as conn:
                conn.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
                mode_row = conn.execute("PRAGMA journal_mode = WAL").fetchone()
                if not mode_row:
                    raise LedgerUnavailable("failed to enable WAL mode")
                conn.execute(_SCHEMA_SQL)
        except (sqlite3.Error, OSError) as exc:
            raise LedgerUnavailable(
                f"failed to initialize ledger at {self._path}: {exc}"
            ) from exc

        self._bootstrapped = True

    def _connect(self) -> sqlite3.Connection:
        if not self._path.is_file():
            raise LedgerUnavailable(f"ledger file missing: {self._path}")
        try:
            conn = sqlite3.connect(
                f"file:{self._path}?mode=rw",
                uri=True,
                timeout=self._busy_timeout_ms / 1000.0,
                isolation_level=None,
            )
            conn.row_factory = sqlite3.Row
            conn.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
            return conn
        except (sqlite3.Error, OSError) as exc:
            raise LedgerUnavailable(f"cannot open ledger {self._path}: {exc}") from exc

    def run_immediate(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """Execute ``fn(conn)`` inside a single ``BEGIN IMMEDIATE`` transaction."""
        conn = self._connect()
        try:
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.Error as exc:
                raise LedgerUnavailable(
                    f"ledger unavailable on BEGIN IMMEDIATE: {exc}"
                ) from exc
            try:
                result = fn(conn)
                conn.execute("COMMIT")
                return result
            except LedgerUnavailable:
                with contextlib.suppress(sqlite3.Error):
                    conn.execute("ROLLBACK")
                raise
            except sqlite3.Error as exc:
                with contextlib.suppress(sqlite3.Error):
                    conn.execute("ROLLBACK")
                raise LedgerUnavailable(f"ledger query failed: {exc}") from exc
            except Exception:
                with contextlib.suppress(sqlite3.Error):
                    conn.execute("ROLLBACK")
                raise
        finally:
            with contextlib.suppress(sqlite3.Error):
                conn.close()

    @staticmethod
    def expire_stale_reservations(
        conn: sqlite3.Connection,
        *,
        now: datetime,
        ttl_minutes: int,
    ) -> int:
        """Convert open reservations older than ``ttl_minutes`` into full commits."""
        open_rows = conn.execute(
            """
            SELECT r.id, r.request_id, r.model, r.usd, r.created_at, r.window_start, r.tag, r.mcp_class
            FROM entries r
            WHERE r.kind = 'reserve'
              AND NOT EXISTS (
                  SELECT 1 FROM entries c
                  WHERE c.request_id = r.request_id
                    AND c.kind IN ('commit', 'release')
              )
            ORDER BY r.id ASC
            """
        ).fetchall()

        expired_count = 0
        ttl_delta = timedelta(minutes=ttl_minutes)
        now_iso = now.astimezone(UTC).isoformat()

        for row in open_rows:
            try:
                created_dt = parse_utc_iso(row["created_at"])
            except Exception as exc:
                raise LedgerUnavailable(
                    f"corrupt created_at in entries id={row['id']}: {exc}"
                ) from exc
            age = now - created_dt
            is_expired = age > ttl_delta if ttl_minutes == 0 else age >= ttl_delta
            if is_expired:
                conn.execute(
                    """
                    INSERT INTO entries (
                        kind, request_id, model, usd, created_at, window_start, tag, mcp_class
                    ) VALUES ('commit', ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["request_id"],
                        row["model"],
                        row["usd"],
                        now_iso,
                        row["window_start"],
                        row["tag"] or "ttl_expired",
                        int(row["mcp_class"] or 0),
                    ),
                )
                expired_count += 1
        return expired_count

    @staticmethod
    def compute_window_snapshot(
        conn: sqlite3.Connection,
        *,
        window_start: datetime,
        reset_at: datetime,
        as_of: datetime | None,
    ) -> WindowSnapshot:
        """Compute committed, open reservations, latch state, and overrun count in window."""
        rows = conn.execute(
            """
            SELECT id, kind, request_id, model, usd, created_at, window_start, tag, mcp_class
            FROM entries
            ORDER BY id ASC
            """
        ).fetchall()

        win_start_iso = window_start.astimezone(UTC).isoformat()

        def _in_window(row: sqlite3.Row) -> bool:
            if row["window_start"] == win_start_iso:
                return True
            try:
                created_dt = parse_utc_iso(row["created_at"])
            except Exception as exc:
                raise LedgerUnavailable(
                    f"corrupt timestamp in entries id={row['id']}: {exc}"
                ) from exc
            return window_start <= created_dt < reset_at

        def _parse_usd(row: sqlite3.Row) -> Decimal:
            try:
                return Decimal(str(row["usd"]))
            except Exception as exc:
                raise LedgerUnavailable(
                    f"corrupt usd in entries id={row['id']}: {exc}"
                ) from exc

        terminal_req_ids: set[str] = set()
        reserve_by_req_id: dict[str, sqlite3.Row] = {}
        commit_by_req_id: dict[str, sqlite3.Row] = {}
        unlinked_commits: list[sqlite3.Row] = []
        latch_rows: list[sqlite3.Row] = []

        for row in rows:
            kind = row["kind"]
            req_id = row["request_id"]
            if kind == "reserve" and req_id:
                reserve_by_req_id[req_id] = row
            elif kind == "commit":
                if req_id:
                    terminal_req_ids.add(req_id)
                    commit_by_req_id[req_id] = row
                else:
                    unlinked_commits.append(row)
            elif kind == "release" and req_id:
                terminal_req_ids.add(req_id)
            elif kind == "latch":
                latch_rows.append(row)

        committed_usd = Decimal("0")
        open_usd = Decimal("0")
        open_count = 0
        open_mcp_count = 0
        overrun_count = 0
        spend_since_as_of = Decimal("0")

        for req_id, c_row in commit_by_req_id.items():
            r_row = reserve_by_req_id.get(req_id)
            in_win = _in_window(c_row) or (r_row is not None and _in_window(r_row))
            c_usd = _parse_usd(c_row)
            if in_win:
                committed_usd += c_usd
                if r_row is not None and c_usd > _parse_usd(r_row):
                    overrun_count += 1
            if as_of is not None:
                ref_row = r_row if r_row is not None else c_row
                if parse_utc_iso(ref_row["created_at"]) >= as_of:
                    spend_since_as_of += c_usd

        for c_row in unlinked_commits:
            c_usd = _parse_usd(c_row)
            if _in_window(c_row):
                committed_usd += c_usd
            if as_of is not None and parse_utc_iso(c_row["created_at"]) >= as_of:
                spend_since_as_of += c_usd

        for req_id, r_row in reserve_by_req_id.items():
            if req_id in terminal_req_ids:
                continue
            r_usd = _parse_usd(r_row)
            if _in_window(r_row):
                open_usd += r_usd
                open_count += 1
                if int(r_row["mcp_class"] or 0) != 0:
                    open_mcp_count += 1
            if as_of is not None and parse_utc_iso(r_row["created_at"]) >= as_of:
                spend_since_as_of += r_usd

        latch_active = False
        latch_reason: str | None = None
        for l_row in latch_rows:
            if l_row["window_start"] == win_start_iso:
                latch_active = True
                latch_reason = l_row["tag"] or "credit_balance_too_low"

        return WindowSnapshot(
            committed_usd=committed_usd,
            open_reservations_usd=open_usd,
            open_reservation_count=open_count,
            open_mcp_count=open_mcp_count,
            spend_since_as_of_usd=spend_since_as_of,
            latch_active=latch_active,
            latch_reason=latch_reason,
            overrun_count=overrun_count,
        )

    @staticmethod
    def recent_entries(conn: sqlite3.Connection, limit: int = 20) -> list[LedgerEntry]:
        """Return the last ``limit`` ledger entries ordered by id descending."""
        rows = conn.execute(
            """
            SELECT id, kind, request_id, model, usd, created_at, window_start, tag, mcp_class
            FROM entries
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        out: list[LedgerEntry] = []
        for row in rows:
            try:
                usd_val = Decimal(str(row["usd"]))
            except Exception as exc:
                raise LedgerUnavailable(
                    f"corrupt usd in entries id={row['id']}: {exc}"
                ) from exc
            out.append(
                LedgerEntry(
                    id=int(row["id"]),
                    kind=str(row["kind"]),
                    request_id=row["request_id"],
                    model=row["model"],
                    usd=usd_val,
                    created_at=str(row["created_at"]),
                    window_start=str(row["window_start"]),
                    tag=row["tag"],
                    mcp_class=bool(row["mcp_class"]),
                )
            )
        return out

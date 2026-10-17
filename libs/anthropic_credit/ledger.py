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

_ENTRIES_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY,
    kind TEXT CHECK(kind IN ('reserve','commit','release','latch')),
    request_id TEXT,
    model TEXT,
    usd TEXT,
    created_at TEXT,
    window_start TEXT,
    tag TEXT,
    caller_tag TEXT,
    mcp_class INTEGER
);
"""

_META_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_SCHEMA_SQL = _ENTRIES_SCHEMA_SQL

_WINDOW_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_entries_window_created "
    "ON entries(window_start, created_at)"
)
_REQUEST_KIND_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_entries_request_kind "
    "ON entries(request_id, kind)"
)
_CREATED_AT_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_entries_created_at ON entries(created_at)"
)
_RESERVE_UNIQUE_INDEX_SQL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_reserve_request "
    "ON entries(request_id) WHERE kind = 'reserve'"
)

_LEDGER_SCHEMA_VERSION = "2"

_UNSAFE_FS_TYPES = frozenset(
    {"nfs", "nfs4", "cifs", "smb3", "fuse.sshfs", "ceph"}
)


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
    caller_tag: str | None
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
            "caller_tag": self.caller_tag,
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


def _fs_type_for_path(
    resolved: Path,
    *,
    mounts_table: list[tuple[str, str]] | None = None,
) -> str | None:
    """Return filesystem type for ``resolved`` from ``/proc/mounts`` (injectable in tests)."""
    if mounts_table is None:
        try:
            mounts_text = Path("/proc/mounts").read_text(encoding="utf-8")
        except OSError:
            return None
        mounts_table = []
        for line in mounts_text.splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            mounts_table.append((parts[1], parts[2]))

    path_str = str(resolved)
    best_mount = ""
    best_fstype: str | None = None
    for mount_point, fstype in mounts_table:
        if path_str == mount_point or path_str.startswith(mount_point.rstrip("/") + "/"):
            if len(mount_point) >= len(best_mount):
                best_mount = mount_point
                best_fstype = fstype
    return best_fstype


def validate_ledger_path(
    raw_path: Path | str,
    *,
    mounts_table: list[tuple[str, str]] | None = None,
) -> Path:
    """Expand ``~`` and refuse paths under ``/mnt/`` or unsafe FS types (K38)."""
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
    fstype = _fs_type_for_path(resolved, mounts_table=mounts_table)
    if fstype is not None and fstype.lower() in _UNSAFE_FS_TYPES:
        raise LedgerUnavailable(
            f"ledger_path on refused filesystem type {fstype!r} (K38): {resolved}"
        )
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
        created_parent = False
        try:
            if not parent.is_dir():
                parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                created_parent = True
            if created_parent:
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
                conn.execute(_ENTRIES_SCHEMA_SQL)
                conn.execute(_META_SCHEMA_SQL)
                conn.execute(_WINDOW_INDEX_SQL)
                conn.execute(_REQUEST_KIND_INDEX_SQL)
                conn.execute(_CREATED_AT_INDEX_SQL)
                conn.execute(_RESERVE_UNIQUE_INDEX_SQL)
        except (sqlite3.Error, OSError) as exc:
            raise LedgerUnavailable(
                f"failed to initialize ledger at {self._path}: {exc}"
            ) from exc

        self._bootstrapped = True

    @staticmethod
    def _ensure_openable(conn: sqlite3.Connection) -> None:
        schema_row = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version' LIMIT 1"
        ).fetchone()
        if schema_row is None or str(schema_row[0]) != _LEDGER_SCHEMA_VERSION:
            raise LedgerUnavailable("schema_mismatch")
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'instance_id' LIMIT 1"
        ).fetchone()
        if row is None:
            raise LedgerUnavailable(
                "ledger missing meta.instance_id — run anthropic-credit-ledger-init"
            )

    def _connect(self) -> sqlite3.Connection:
        if not self._path.is_file():
            raise LedgerUnavailable(f"ledger file missing: {self._path}")
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(
                f"file:{self._path}?mode=rw",
                uri=True,
                timeout=self._busy_timeout_ms / 1000.0,
                isolation_level=None,
            )
            conn.row_factory = sqlite3.Row
            conn.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
            CreditLedger._ensure_openable(conn)
            conn.execute(_WINDOW_INDEX_SQL)
            conn.execute(_REQUEST_KIND_INDEX_SQL)
            conn.execute(_CREATED_AT_INDEX_SQL)
            conn.execute(_RESERVE_UNIQUE_INDEX_SQL)
            return conn
        except LedgerUnavailable:
            if conn is not None:
                with contextlib.suppress(sqlite3.Error):
                    conn.close()
            raise
        except (sqlite3.Error, OSError) as exc:
            if conn is not None:
                with contextlib.suppress(sqlite3.Error):
                    conn.close()
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
        window_start: datetime | None = None,
        reset_at: datetime | None = None,
    ) -> int:
        """Convert open reservations older than ``ttl_minutes`` into full commits."""
        window_clause = ""
        window_params: tuple[str, ...] = ()
        if window_start is not None and reset_at is not None:
            win_start_iso = window_start.astimezone(UTC).isoformat()
            reset_iso = reset_at.astimezone(UTC).isoformat()
            window_clause = """
              AND (
                r.window_start = ?
                OR (r.created_at >= ? AND r.created_at < ?)
              )
            """
            window_params = (win_start_iso, win_start_iso, reset_iso)

        open_rows = conn.execute(
            f"""
            SELECT r.id, r.request_id, r.model, r.usd, r.created_at, r.window_start, r.tag, r.caller_tag, r.mcp_class
            FROM entries r
            WHERE r.kind = 'reserve'
              AND NOT EXISTS (
                  SELECT 1 FROM entries c
                  WHERE c.request_id = r.request_id
                    AND c.kind IN ('commit', 'release')
              )
              {window_clause}
            ORDER BY r.id ASC
            """,
            window_params,
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
                        kind, request_id, model, usd, created_at, window_start, tag, caller_tag, mcp_class
                    ) VALUES ('commit', ?, ?, ?, ?, ?, 'ttl_expired', ?, ?)
                    """,
                    (
                        row["request_id"],
                        row["model"],
                        row["usd"],
                        now_iso,
                        row["window_start"],
                        row["caller_tag"] or row["tag"],
                        int(row["mcp_class"] or 0),
                    ),
                )
                expired_count += 1
        return expired_count

    @staticmethod
    def compute_spend_since_as_of(
        conn: sqlite3.Connection,
        *,
        as_of: datetime,
    ) -> Decimal:
        """Sum committed and open-reserve USD for all rows with activity since ``as_of``."""
        as_of_dt = as_of.astimezone(UTC)
        as_of_iso = as_of_dt.isoformat()
        rows = conn.execute(
            """
            SELECT id, kind, request_id, usd, created_at, tag, caller_tag, mcp_class
            FROM entries
            WHERE created_at >= ?
            ORDER BY id ASC
            """,
            (as_of_iso,),
        ).fetchall()

        def _parse_usd(row: sqlite3.Row) -> Decimal:
            try:
                return Decimal(str(row["usd"]))
            except Exception as exc:
                raise LedgerUnavailable(
                    f"corrupt usd in entries id={row['id']}: {exc}"
                ) from exc

        reserve_by_req: dict[str, sqlite3.Row] = {}
        commits_by_req: dict[str, list[sqlite3.Row]] = {}
        released_req_ids: set[str] = set()
        unlinked_commits: list[sqlite3.Row] = []

        for row in rows:
            kind = row["kind"]
            req_id = row["request_id"]
            if kind == "reserve" and req_id:
                reserve_by_req[req_id] = row
            elif kind == "commit":
                if req_id:
                    commits_by_req.setdefault(req_id, []).append(row)
                else:
                    unlinked_commits.append(row)
            elif kind == "release" and req_id:
                released_req_ids.add(req_id)

        def _row_at_or_after_as_of(row: sqlite3.Row | None) -> bool:
            if row is None:
                return False
            return parse_utc_iso(row["created_at"]) >= as_of_dt

        def _request_touches_as_of(
            req_id: str,
            *,
            reserve_row: sqlite3.Row | None,
            commit_rows: list[sqlite3.Row],
        ) -> bool:
            if _row_at_or_after_as_of(reserve_row):
                return True
            return any(_row_at_or_after_as_of(c) for c in commit_rows)

        spend = Decimal("0")
        for req_id, commit_rows in commits_by_req.items():
            reserve_row = reserve_by_req.get(req_id)
            commit_sum = sum(_parse_usd(c) for c in commit_rows)
            if _request_touches_as_of(
                req_id, reserve_row=reserve_row, commit_rows=commit_rows
            ):
                spend += commit_sum

        for c_row in unlinked_commits:
            if _row_at_or_after_as_of(c_row):
                spend += _parse_usd(c_row)

        for req_id, r_row in reserve_by_req.items():
            if req_id in released_req_ids:
                continue
            if commits_by_req.get(req_id):
                continue
            if _row_at_or_after_as_of(r_row):
                spend += _parse_usd(r_row)

        return spend

    @staticmethod
    def compute_window_snapshot(
        conn: sqlite3.Connection,
        *,
        window_start: datetime,
        reset_at: datetime,
        as_of: datetime | None,
        now: datetime | None = None,
        reservation_ttl_minutes: int = 15,
    ) -> WindowSnapshot:
        """Compute committed, open reservations, latch state, and overrun count in window."""
        win_start_iso = window_start.astimezone(UTC).isoformat()
        reset_iso = reset_at.astimezone(UTC).isoformat()
        now_dt = (now or datetime.now(tz=UTC)).astimezone(UTC)
        mcp_serialize_minutes = max(1, int(reservation_ttl_minutes)) * 4

        rows = conn.execute(
            """
            SELECT id, kind, request_id, model, usd, created_at, window_start, tag, mcp_class
            FROM entries
            WHERE window_start = ?
               OR (created_at >= ? AND created_at < ?)
            ORDER BY id ASC
            """,
            (win_start_iso, win_start_iso, reset_iso),
        ).fetchall()

        def _parse_usd(row: sqlite3.Row) -> Decimal:
            try:
                return Decimal(str(row["usd"]))
            except Exception as exc:
                raise LedgerUnavailable(
                    f"corrupt usd in entries id={row['id']}: {exc}"
                ) from exc

        reserve_by_req: dict[str, sqlite3.Row] = {}
        commits_by_req: dict[str, list[sqlite3.Row]] = {}
        released_req_ids: set[str] = set()
        latch_rows: list[sqlite3.Row] = []
        unlinked_commits: list[sqlite3.Row] = []

        for row in rows:
            kind = row["kind"]
            req_id = row["request_id"]
            if kind == "reserve" and req_id:
                reserve_by_req[req_id] = row
            elif kind == "commit":
                if req_id:
                    commits_by_req.setdefault(req_id, []).append(row)
                else:
                    unlinked_commits.append(row)
            elif kind == "release" and req_id:
                released_req_ids.add(req_id)
            elif kind == "latch":
                latch_rows.append(row)

        committed_usd = Decimal("0")
        open_usd = Decimal("0")
        open_count = 0
        open_mcp_count = 0
        overrun_count = 0
        spend_since_as_of = Decimal("0")

        for req_id, commit_rows in commits_by_req.items():
            reserve_row = reserve_by_req.get(req_id)
            commit_sum = sum(_parse_usd(c) for c in commit_rows)
            in_win = any(
                c["window_start"] == win_start_iso
                or (
                    win_start_iso <= str(c["created_at"]) < reset_iso
                )
                for c in commit_rows
            ) or (reserve_row is not None and reserve_row["window_start"] == win_start_iso)
            if in_win:
                committed_usd += commit_sum
                if reserve_row is not None and commit_sum > _parse_usd(reserve_row):
                    overrun_count += 1

        for c_row in unlinked_commits:
            c_usd = _parse_usd(c_row)
            committed_usd += c_usd

        for req_id, r_row in reserve_by_req.items():
            if req_id in released_req_ids:
                continue
            commit_rows = commits_by_req.get(req_id, [])
            has_commit = bool(commit_rows)
            r_usd = _parse_usd(r_row)
            mcp_class = int(r_row["mcp_class"] or 0) != 0
            reserve_created = parse_utc_iso(r_row["created_at"])
            reserve_age_min = (now_dt - reserve_created).total_seconds() / 60.0

            only_ttl = has_commit and all(
                (c["tag"] or "") == "ttl_expired" for c in commit_rows
            )
            finalized = has_commit and not only_ttl

            if not has_commit:
                reserve_in_win = r_row["window_start"] == win_start_iso or (
                    win_start_iso <= str(r_row["created_at"]) < reset_iso
                )
                if reserve_in_win:
                    open_usd += r_usd
                    open_count += 1
                    if mcp_class:
                        open_mcp_count += 1
            elif mcp_class and not finalized and reserve_age_min < mcp_serialize_minutes:
                open_mcp_count += 1

        latch_active = False
        latch_reason: str | None = None
        for l_row in latch_rows:
            if l_row["window_start"] == win_start_iso:
                latch_active = True
                latch_reason = l_row["tag"] or "credit_balance_too_low"

        if as_of is not None:
            spend_since_as_of = CreditLedger.compute_spend_since_as_of(
                conn, as_of=as_of
            )

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
            SELECT id, kind, request_id, model, usd, created_at, window_start, tag, caller_tag, mcp_class
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
                    caller_tag=row["caller_tag"],
                    mcp_class=bool(row["mcp_class"]),
                )
            )
        return out

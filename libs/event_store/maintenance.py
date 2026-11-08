"""Offline event-store maintenance: retention deletes, index build, checkpoint."""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

from .retention import HEARTBEAT_SIGNALS
from .retention_config import load_retention_settings
from .schema import build_model_id_ts_index

logger = logging.getLogger(__name__)

_LIVE_DB = Path("/home/io/.events/events.db").resolve()
_DEFAULT_QUERY_SOCK = Path("/tmp/universal-protocol/events-query.sock")
_SESSION_BOUNDARY_SIGNAL = "event.service.started"
_RETENTION_TABLES = ("events", "request_snapshots", "evaluations")
_RETENTION_PK = {"events": "seq", "request_snapshots": "seq", "evaluations": "id"}
_BATCH_SIZE = 1000
_MAINT_CACHE_KIB = 262144
_MAINT_MMAP_BYTES = 256 * 1024**2


def _emit(step: str, **fields: Any) -> None:
    payload = {"step": step, "elapsed_s": fields.pop("elapsed_s", 0.0), **fields}
    print(json.dumps(payload), flush=True)


def _query_sock_path() -> Path:
    raw = os.environ.get("EVENTS_QUERY_SOCK", str(_DEFAULT_QUERY_SOCK))
    return Path(os.path.expanduser(raw))


def _query_socket_answers(sock: Path | None = None) -> bool:
    """True when the event-service query UDS responds to GET /health."""
    path = sock or _query_sock_path()
    if not path.is_socket():
        return False
    try:
        from transport_utils import make_sync_client

        with make_sync_client(f"unix://{path}", timeout=0.5) as client:
            resp = client.get("/health")
            return resp.status_code == 200
    except Exception:
        return False


def _refuse_live_db(db_path: Path, *, live_window: bool) -> None:
    if db_path.resolve() == _LIVE_DB and not live_window:
        raise SystemExit(
            f"Refusing maintenance on live db {_LIVE_DB}; "
            "pass --live-maintenance-window if intentional"
        )
    if db_path.resolve() == _LIVE_DB and live_window and _query_socket_answers():
        raise SystemExit(
            "Refusing live maintenance: event-service query socket answered /health; "
            "stop the service first"
        )


def _refuse_while_query_live(action: str) -> None:
    if _query_socket_answers():
        raise SystemExit(
            f"Refusing {action}: event-service query socket answered /health; "
            "stop the service first"
        )


def _preflight_exclusive(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.execute("PRAGMA busy_timeout=0")
    try:
        conn.execute("BEGIN EXCLUSIVE")
        conn.execute("ROLLBACK")
    except sqlite3.OperationalError as exc:
        conn.close()
        raise SystemExit(f"Database is busy (service likely running): {exc}") from exc
    conn.close()
    conn = _open_maintenance(db_path)
    return conn


def _open_maintenance(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA temp_store=FILE")
    conn.execute(f"PRAGMA cache_size={-_MAINT_CACHE_KIB}")
    conn.execute(f"PRAGMA mmap_size={_MAINT_MMAP_BYTES}")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _session_cutoff(conn: sqlite3.Connection) -> int | None:
    row = conn.execute(
        "SELECT MAX(ts_unix_ms) AS ts FROM events WHERE signal = ?",
        (_SESSION_BOUNDARY_SIGNAL,),
    ).fetchone()
    if row is None or row["ts"] is None:
        return None
    return int(row["ts"])


def _count_where(conn: sqlite3.Connection, table: str, where: str, params: tuple[Any, ...]) -> int:
    row = conn.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}", params).fetchone()
    return int(row["n"] if row is not None else 0)


def _dry_run_counts(conn: sqlite3.Connection, *, age_cutoff: int, session_cutoff: int | None) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in _RETENTION_TABLES:
        counts[f"age_{table}"] = _count_where(conn, table, "ts_unix_ms < ?", (age_cutoff,))
    if session_cutoff is not None:
        counts["debug"] = _count_where(
            conn,
            "events",
            "role = 'debug' AND ts_unix_ms < ?",
            (session_cutoff,),
        )
        placeholders = ", ".join("?" for _ in HEARTBEAT_SIGNALS)
        counts["heartbeat"] = _count_where(
            conn,
            "events",
            f"signal IN ({placeholders}) AND ts_unix_ms < ?",
            (*sorted(HEARTBEAT_SIGNALS), session_cutoff),
        )
    else:
        counts["debug"] = 0
        counts["heartbeat"] = 0
    return counts


def _delete_keyset(
    conn: sqlite3.Connection,
    table: str,
    where_sql: str,
    params: tuple[Any, ...],
) -> int:
    pk = _RETENTION_PK[table]
    total = 0
    last_pk = 0
    select_sql = (
        f"SELECT {pk} FROM {table} WHERE {pk} > ? AND ({where_sql}) "
        f"ORDER BY {pk} LIMIT ?"
    )
    while True:
        picked = conn.execute(select_sql, (last_pk, *params, _BATCH_SIZE)).fetchall()
        if not picked:
            conn.commit()
            break
        ids = [int(row[0]) for row in picked]
        last_pk = ids[-1]
        placeholders = ", ".join("?" for _ in ids)
        cursor = conn.execute(
            f"DELETE FROM {table} WHERE {pk} IN ({placeholders})",
            ids,
        )
        deleted = int(cursor.rowcount or 0)
        conn.commit()
        total += deleted
        if deleted < _BATCH_SIZE:
            break
    return total


def _execute_retention(conn: sqlite3.Connection, *, age_cutoff: int, session_cutoff: int | None) -> dict[str, int]:
    deleted: dict[str, int] = {}
    for table in _RETENTION_TABLES:
        deleted[f"age_{table}"] = _delete_keyset(conn, table, "ts_unix_ms < ?", (age_cutoff,))
    if session_cutoff is not None:
        deleted["debug"] = _delete_keyset(
            conn,
            "events",
            "role = 'debug' AND ts_unix_ms < ?",
            (session_cutoff,),
        )
        placeholders = ", ".join("?" for _ in HEARTBEAT_SIGNALS)
        deleted["heartbeat"] = _delete_keyset(
            conn,
            "events",
            f"signal IN ({placeholders}) AND ts_unix_ms < ?",
            (*sorted(HEARTBEAT_SIGNALS), session_cutoff),
        )
    else:
        deleted["debug"] = 0
        deleted["heartbeat"] = 0
    return deleted


def _vacuum_free_space(db_path: Path) -> None:
    st = db_path.stat()
    wal = Path(f"{db_path}-wal")
    wal_size = wal.stat().st_size if wal.is_file() else 0
    need = int((st.st_size + wal_size) * 1.2)
    free = shutil.disk_usage(db_path.parent).free
    if free < need:
        raise SystemExit(
            f"Insufficient disk for vacuum: need~={need} free={free} "
            f"db={st.st_size} wal={wal_size}"
        )


def run_maintenance(argv: list[str] | None = None) -> int:
    settings = load_retention_settings()
    parser = argparse.ArgumentParser(prog="event_store.maintenance")
    parser.add_argument("--db", required=True, help="Path to events SQLite database")
    parser.add_argument(
        "--days",
        type=int,
        default=settings.retention_days,
        help="Age retention in days (default from retention config)",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply deletes and maintenance (default is dry-run counts only)",
    )
    parser.add_argument("--vacuum", action="store_true", help="Run VACUUM after deletes")
    parser.add_argument("--skip-index", action="store_true", help="Skip idx_model_id_ts build")
    parser.add_argument(
        "--live-maintenance-window",
        action="store_true",
        help="Allow maintenance on the live events.db path",
    )
    args = parser.parse_args(argv)
    db_path = Path(os.path.expanduser(args.db))
    if not db_path.is_file():
        raise SystemExit(f"Database not found: {db_path}")
    execute = bool(args.execute)
    dry_run = not execute

    started = time.perf_counter()
    _refuse_live_db(db_path, live_window=args.live_maintenance_window)
    if args.vacuum:
        _refuse_while_query_live("--vacuum")
        _vacuum_free_space(db_path)
    conn = _preflight_exclusive(db_path)
    _emit("preflight", elapsed_s=time.perf_counter() - started, ok=True)

    age_cutoff = int(time.time() * 1000) - args.days * 86400 * 1000
    session_cutoff = _session_cutoff(conn)

    t0 = time.perf_counter()
    counts = _dry_run_counts(conn, age_cutoff=age_cutoff, session_cutoff=session_cutoff)
    _emit("dry_run", elapsed_s=time.perf_counter() - t0, counts=counts, dry_run=dry_run)

    if dry_run:
        conn.close()
        return 0

    t1 = time.perf_counter()
    deleted = _execute_retention(conn, age_cutoff=age_cutoff, session_cutoff=session_cutoff)
    _emit("retention_execute", elapsed_s=time.perf_counter() - t1, deleted=deleted)

    if not args.skip_index:
        _refuse_while_query_live("idx_model_id_ts build")
        t2 = time.perf_counter()
        created = build_model_id_ts_index(conn)
        _emit("index_model_id_ts", elapsed_s=time.perf_counter() - t2, created=created)

    if args.vacuum:
        t3 = time.perf_counter()
        conn.execute("PRAGMA temp_store=FILE")
        conn.execute("VACUUM")
        _emit("vacuum", elapsed_s=time.perf_counter() - t3)

    t4 = time.perf_counter()
    row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    _emit(
        "wal_checkpoint",
        elapsed_s=time.perf_counter() - t4,
        result=list(row) if row is not None else None,
    )
    conn.close()
    return 0


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    try:
        code = run_maintenance()
    except SystemExit as exc:
        if exc.code not in (None, 0):
            print(str(exc), file=sys.stderr)
        raise
    sys.exit(code)


if __name__ == "__main__":
    main()

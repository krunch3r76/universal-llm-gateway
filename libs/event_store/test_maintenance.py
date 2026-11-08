"""Maintenance CLI dry-run safety and execute path."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from unittest import mock

import pytest

from event_store.maintenance import _query_socket_live, run_maintenance
from event_store.schema import _SCHEMA_SQL


def _make_db(path: Path, *, rows: int = 5) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA_SQL)
    now = int(time.time() * 1000)
    for i in range(rows):
        conn.execute(
            "INSERT INTO events (signal, role, scope, ts_unix_ms, timestamp, source, payload) "
            "VALUES (?, 'observation', 'global', ?, 't', 'test', '{}')",
            ("test.signal", now - i * 86400 * 1000),
        )
    conn.commit()
    conn.close()


@pytest.mark.offline
def test_dry_run_does_not_delete(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _make_db(db, rows=10)
    run_maintenance(["--db", str(db)])
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.close()
    assert n == 10


@pytest.mark.offline
def test_refuses_live_path_without_flag(tmp_path: Path) -> None:
    live = Path("/home/io/.events/events.db")
    if not live.is_file():
        pytest.skip("live events.db not present")
    with pytest.raises(SystemExit):
        run_maintenance(["--db", str(live)])


@pytest.mark.offline
def test_refuses_when_write_lock_held(tmp_path: Path) -> None:
    db = tmp_path / "busy.db"
    _make_db(db)
    holder = sqlite3.connect(db, isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(SystemExit, match="busy"):
            run_maintenance(["--db", str(db)])
    finally:
        holder.execute("ROLLBACK")
        holder.close()


@pytest.mark.offline
def test_live_window_refuses_when_query_socket_answers(tmp_path: Path) -> None:
    live = Path("/home/io/.events/events.db")
    if not live.is_file():
        pytest.skip("live events.db not present")
    with (
        mock.patch(
            "event_store.maintenance._query_socket_live", return_value=True
        ),
        pytest.raises(SystemExit, match="query socket"),
    ):
        run_maintenance(["--db", str(live), "--live-maintenance-window"])


@pytest.mark.offline
def test_vacuum_refuses_when_query_socket_answers(tmp_path: Path) -> None:
    live = Path("/home/io/.events/events.db")
    if not live.is_file():
        pytest.skip("live events.db not present")
    with (
        mock.patch(
            "event_store.maintenance._query_socket_live", return_value=True
        ),
        pytest.raises(SystemExit, match="query socket is live"),
    ):
        run_maintenance(["--db", str(live), "--execute", "--vacuum", "--live-maintenance-window"])


@pytest.mark.offline
def test_execute_deletes_and_builds_index(tmp_path: Path) -> None:
    db = tmp_path / "exec.db"
    _make_db(db, rows=20)
    with mock.patch("event_store.maintenance._query_socket_live", return_value=False):
        run_maintenance(["--db", str(db), "--execute", "--days", "3"])
    conn = sqlite3.connect(db)
    remaining = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    idx = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='idx_model_id_ts'"
    ).fetchone()
    conn.close()
    assert remaining < 20
    assert idx is not None


@pytest.mark.offline
def test_query_socket_probe_no_socket(tmp_path: Path) -> None:
    missing = tmp_path / "missing.sock"
    assert _query_socket_live(missing) is False


@pytest.mark.offline
def test_query_socket_probe_stale_socket_file(tmp_path: Path) -> None:
    stale = tmp_path / "stale.sock"
    stale.write_text("not a socket", encoding="utf-8")
    assert _query_socket_live(stale) is False


@pytest.mark.offline
def test_query_socket_probe_live_unix_server(tmp_path: Path) -> None:
    import socket
    import threading

    sock_path = tmp_path / "live.sock"

    def _serve() -> None:
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(sock_path))
        srv.listen(1)
        conn, _ = srv.accept()
        conn.recv(4096)
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        conn.close()
        srv.close()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    deadline = time.time() + 2.0
    while not sock_path.exists() and time.time() < deadline:
        time.sleep(0.01)
    assert _query_socket_live(sock_path) is True
    thread.join(timeout=2.0)


@pytest.mark.offline
def test_query_socket_probe_slow_server_still_live(tmp_path: Path) -> None:
    import socket
    import threading

    sock_path = tmp_path / "slow.sock"

    def _serve() -> None:
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(sock_path))
        srv.listen(1)
        conn, _ = srv.accept()
        time.sleep(2.0)
        conn.close()
        srv.close()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    deadline = time.time() + 2.0
    while not sock_path.exists() and time.time() < deadline:
        time.sleep(0.01)
    assert _query_socket_live(sock_path) is True
    thread.join(timeout=3.0)


@pytest.mark.offline
def test_vacuum_on_copy_not_blocked_by_live_probe(tmp_path: Path) -> None:
    db = tmp_path / "copy.db"
    _make_db(db)
    with mock.patch("event_store.maintenance._query_socket_live", return_value=True):
        run_maintenance(["--db", str(db), "--execute", "--vacuum", "--days", "365"])

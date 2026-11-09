"""Maintenance CLI dry-run safety and execute path."""

from __future__ import annotations

import socket
import sqlite3
import threading
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
        mock.patch("event_store.maintenance._query_socket_live", return_value=True),
        pytest.raises(SystemExit, match="query socket"),
    ):
        run_maintenance(["--db", str(live), "--live-maintenance-window"])


@pytest.mark.offline
def test_vacuum_refuses_when_query_socket_answers(tmp_path: Path) -> None:
    live = Path("/home/io/.events/events.db")
    if not live.is_file():
        pytest.skip("live events.db not present")
    with (
        mock.patch("event_store.maintenance._query_socket_live", return_value=True),
        pytest.raises(SystemExit, match="query socket is live"),
    ):
        run_maintenance(
            ["--db", str(live), "--execute", "--vacuum", "--live-maintenance-window"]
        )


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
    plain = tmp_path / "not-a-socket.txt"
    plain.write_text("not a socket", encoding="utf-8")
    assert _query_socket_live(plain) is False

    stale = tmp_path / "stale.sock"
    bound = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    bound.bind(str(stale))
    bound.close()
    assert stale.is_socket()
    assert _query_socket_live(stale) is False


def _uds_accept_loop(sock_path: Path, on_conn) -> None:
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(sock_path))
    srv.listen(1)
    stop = threading.Event()

    def _serve() -> None:
        while not stop.is_set():
            try:
                srv.settimeout(0.5)
                conn, _ = srv.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            try:
                on_conn(conn)
            except OSError:
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass
        try:
            srv.close()
        except OSError:
            pass

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    return stop, thread, srv


@pytest.mark.offline
def test_query_socket_probe_live_unix_server(tmp_path: Path) -> None:
    sock_path = tmp_path / "live.sock"

    def _handle(conn: socket.socket) -> None:
        try:
            conn.recv(4096)
        except OSError:
            return
        try:
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        except OSError:
            pass

    stop, thread, srv = _uds_accept_loop(sock_path, _handle)
    try:
        assert _query_socket_live(sock_path) is True
    finally:
        stop.set()
        srv.close()
        thread.join(timeout=2.0)


@pytest.mark.offline
def test_query_socket_probe_http_503_listener_live(tmp_path: Path) -> None:
    sock_path = tmp_path / "503.sock"

    def _handle(conn: socket.socket) -> None:
        try:
            conn.recv(4096)
        except OSError:
            return
        try:
            conn.sendall(
                b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\n\r\n"
            )
        except OSError:
            pass

    stop, thread, srv = _uds_accept_loop(sock_path, _handle)
    try:
        assert _query_socket_live(sock_path) is True
    finally:
        stop.set()
        srv.close()
        thread.join(timeout=2.0)


@pytest.mark.offline
def test_query_socket_probe_slow_accept_still_live(tmp_path: Path) -> None:
    sock_path = tmp_path / "slow.sock"

    def _handle(conn: socket.socket) -> None:
        time.sleep(2.0)
        try:
            conn.close()
        except OSError:
            pass

    stop, thread, srv = _uds_accept_loop(sock_path, _handle)
    try:
        assert _query_socket_live(sock_path) is True
    finally:
        stop.set()
        srv.close()
        thread.join(timeout=3.0)


@pytest.mark.offline
def test_vacuum_on_copy_not_blocked_by_live_probe(tmp_path: Path) -> None:
    db = tmp_path / "copy.db"
    _make_db(db)
    with mock.patch("event_store.maintenance._query_socket_live", return_value=True):
        run_maintenance(["--db", str(db), "--execute", "--vacuum", "--days", "365"])

"""SIGTERM with idle peers must exit inside 3s.

manage sends SIGTERM and SIGKILLs after about 8s. An idle ingest connection
used to pin ``Server.wait_closed`` for that whole grace. These cases hold
the sockets the live fleet holds: subscribe websockets, idle publishers, and
a query client that never finishes a request.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest


def _free_tcp_port() -> int:
    """Return a port whose successor is also free (ingest, then query)."""
    for _ in range(20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        if port >= 65535:
            continue
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as nxt:
                nxt.bind(("127.0.0.1", port + 1))
        except OSError:
            continue
        return port
    raise RuntimeError("no consecutive TCP ports")


def _serve(
    db: Path, ingest: Path, query: Path, *, tcp_port: int | None
) -> subprocess.Popen[str]:
    env = os.environ.copy()
    worktree_libs = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = worktree_libs + os.pathsep + env.get("PYTHONPATH", "")
    env["EVENTS_RETENTION_STARTUP"] = "off"
    argv = [
        sys.executable,
        "-m",
        "event_store",
        "serve",
        "--db",
        str(db),
        "--sock",
        str(ingest),
        "--query-sock",
        str(query),
    ]
    if tcp_port is not None:
        argv.extend(
            [
                "--tcp",
                "--tcp-ingest-port",
                str(tcp_port),
                "--tcp-query-port",
                str(tcp_port + 1),
            ]
        )
    return subprocess.Popen(
        argv,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _wait_log(proc: subprocess.Popen[str], needle: str, timeout: float) -> str:
    assert proc.stdout is not None
    deadline = time.monotonic() + timeout
    buf = ""
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if line:
            buf += line
            if needle in buf:
                return buf
        elif proc.poll() is not None:
            break
    raise AssertionError(f"missing {needle!r} in:\n{buf}")


def _sigterm_exit_s(proc: subprocess.Popen[str]) -> float:
    assert proc.pid
    started = time.monotonic()
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=4)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)
        pytest.fail(f"still alive after {time.monotonic() - started:.2f}s")
    return time.monotonic() - started


def _open_tcp(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    sock.connect(("127.0.0.1", port))
    sock.settimeout(None)
    return sock


def _open_uds(path: Path) -> socket.socket:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(5)
    sock.connect(str(path))
    sock.settimeout(None)
    return sock


def _hold_subscribers(
    query_sock: Path,
    count: int,
    ready: threading.Event,
    errors: list[BaseException],
) -> None:
    async def _run() -> None:
        from websockets.asyncio.client import unix_connect

        acked = 0

        async def _one() -> None:
            nonlocal acked
            async with unix_connect(
                str(query_sock),
                uri="ws://localhost/v1/subscribe",
                open_timeout=5,
            ) as ws:
                await ws.send(json.dumps({"type": "subscribe", "filter": {}}))
                ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                if ack.get("type") != "subscribed":
                    raise RuntimeError(f"bad ack {ack}")
                acked += 1
                if acked == count:
                    ready.set()
                await asyncio.sleep(30)

        await asyncio.gather(*(_one() for _ in range(count)))

    try:
        asyncio.run(_run())
    except Exception as exc:
        errors.append(exc)
        ready.set()


@pytest.mark.offline
def test_stop_with_idle_uds_publishers_exits_under_3s(tmp_path: Path) -> None:
    """Two idle UDS publishers must not pin ingest ``wait_closed``."""
    db = tmp_path / "events.db"
    assert str(db).startswith("/tmp")
    proc = _serve(db, tmp_path / "ingest.sock", tmp_path / "query.sock", tcp_port=None)
    socks: list[socket.socket] = []
    try:
        _wait_log(proc, "Event service started", 10)
        socks = [_open_uds(tmp_path / "ingest.sock") for _ in range(2)]
        time.sleep(0.2)
        elapsed = _sigterm_exit_s(proc)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)
        for sock in socks:
            sock.close()
    assert elapsed < 3


@pytest.mark.offline
def test_stop_with_idle_tcp_publishers_exits_under_3s(tmp_path: Path) -> None:
    """Two idle TCP publishers must not pin ingest ``wait_closed``."""
    db = tmp_path / "events.db"
    ingest_port = _free_tcp_port()
    proc = _serve(
        db,
        tmp_path / "ingest.sock",
        tmp_path / "query.sock",
        tcp_port=ingest_port,
    )
    socks: list[socket.socket] = []
    try:
        _wait_log(proc, "Ingest TCP listener", 10)
        socks = [_open_tcp(ingest_port) for _ in range(2)]
        time.sleep(0.2)
        elapsed = _sigterm_exit_s(proc)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)
        for sock in socks:
            sock.close()
    assert elapsed < 3


@pytest.mark.offline
def test_stop_with_subscribers_ingest_and_query_exits_under_3s(tmp_path: Path) -> None:
    """Full peer set: subscribers, idle publishers, and a held query socket."""
    db = tmp_path / "events.db"
    ingest_port = _free_tcp_port()
    query_port = ingest_port + 1
    proc = _serve(
        db,
        tmp_path / "ingest.sock",
        tmp_path / "query.sock",
        tcp_port=ingest_port,
    )
    ready = threading.Event()
    errors: list[BaseException] = []
    thread = threading.Thread(
        target=_hold_subscribers,
        args=(tmp_path / "query.sock", 12, ready, errors),
        daemon=True,
    )
    socks: list[socket.socket] = []
    try:
        _wait_log(proc, "Event service started", 10)
        thread.start()
        assert ready.wait(8), "subscribers did not ack"
        assert not errors, errors
        socks = [
            _open_uds(tmp_path / "ingest.sock"),
            _open_uds(tmp_path / "ingest.sock"),
            _open_tcp(ingest_port),
            _open_tcp(ingest_port),
            _open_tcp(query_port),
            _open_uds(tmp_path / "query.sock"),
        ]
        time.sleep(0.2)
        elapsed = _sigterm_exit_s(proc)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)
        for sock in socks:
            sock.close()
    assert elapsed < 3

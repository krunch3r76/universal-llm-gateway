"""SIGTERM after retention must exit inside manage's grace."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from event_store.store import EventStore


def _seed(db_path: Path, n: int) -> None:
    old = int(time.time() * 1000) - 30 * 86400 * 1000

    async def _run() -> None:
        store = EventStore(db_path)
        await store.open()
        try:
            await store.insert_events(
                [
                    {
                        "signal": "old.evt",
                        "role": "observation",
                        "scope": "global",
                        "ts_unix_ms": old,
                        "timestamp": "2020-01-01T00:00:00Z",
                        "source": "t",
                        "payload": {},
                    }
                    for _ in range(n)
                ]
            )
        finally:
            await store.close()

    asyncio.run(_run())


def _serve(
    db: Path, ingest: Path, query: Path, env: dict[str, str]
) -> subprocess.Popen[str]:
    merged = os.environ.copy()
    merged.update(env)
    worktree_libs = str(Path(__file__).resolve().parents[1])
    merged["PYTHONPATH"] = worktree_libs + os.pathsep + merged.get("PYTHONPATH", "")
    return subprocess.Popen(
        [
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
        ],
        env=merged,
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
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)
        pytest.fail(f"still alive after {time.monotonic() - started:.2f}s")
    return time.monotonic() - started


@pytest.mark.offline
def test_stop_after_retention_pass_exits_under_5s(tmp_path: Path) -> None:
    """A finished dry-run leaves the retention pool up; SIGTERM must still return."""
    db = tmp_path / "events.db"
    _seed(db, 1)
    proc = _serve(
        db,
        tmp_path / "ingest.sock",
        tmp_path / "query.sock",
        {"EVENTS_RETENTION_STARTUP": "dry_run"},
    )
    try:
        _wait_log(proc, "Retention pass dry_run=True", 10)
        elapsed = _sigterm_exit_s(proc)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)
    assert elapsed < 5


@pytest.mark.offline
def test_stop_during_retention_batch_sleep_exits_under_5s(tmp_path: Path) -> None:
    """An in-flight inter-batch sleep must wake on SIGTERM (manage grace is ~8s)."""
    db = tmp_path / "events.db"
    _seed(db, 3)
    proc = _serve(
        db,
        tmp_path / "ingest.sock",
        tmp_path / "query.sock",
        {
            "EVENTS_RETENTION_STARTUP": "run",
            "EVENTS_RETENTION_BATCH_SIZE": "1",
            "EVENTS_RETENTION_BATCH_SLEEP_S": "30",
        },
    )
    try:
        _wait_log(proc, "Retention batch table=events", 10)
        elapsed = _sigterm_exit_s(proc)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)
    assert elapsed < 5

"""AC4/AC5: threading.Lock does not serialise OS processes; flock does."""

from __future__ import annotations

import time
from multiprocessing import Process, Queue
from pathlib import Path

import pytest

from durable_io.atomic import durable_write_text, path_flock

N_PROCS = 8
N_ROUNDS = 3
EXPECTED = N_PROCS * N_ROUNDS


def _rmw_append_write_text(path: Path, marker: str) -> None:
    data = path.read_text(encoding="utf-8") if path.is_file() else ""
    time.sleep(0.002)
    path.write_text(data + marker, encoding="utf-8")


def _worker_threading_lock(path_s: str, wid: int, q: Queue) -> None:
    import threading

    path = Path(path_s)
    lock = threading.Lock()
    for round_i in range(N_ROUNDS):
        marker = f"T{wid}R{round_i}\n"
        with lock:
            _rmw_append_write_text(path, marker)
        q.put(marker)


def _worker_flock_rmw(path_s: str, wid: int, q: Queue) -> None:
    path = Path(path_s)
    for round_i in range(N_ROUNDS):
        marker = f"F{wid}R{round_i}\n"
        with path_flock(path):
            data = path.read_text(encoding="utf-8") if path.is_file() else ""
            time.sleep(0.002)
            durable_write_text(path, data + marker, already_locked=True)
        q.put(marker)


def _worker_two_proc(path_s: str, wid: int, q: Queue) -> None:
    path = Path(path_s)
    for round_i in range(8):
        marker = f"P{wid}R{round_i}\n"
        with path_flock(path):
            data = path.read_text(encoding="utf-8") if path.is_file() else ""
            durable_write_text(path, data + marker, already_locked=True)
        q.put(marker)


def _run(target, path: Path, n_procs: int, n_each: int) -> tuple[list[str], str]:
    q: Queue = Queue()
    procs = [Process(target=target, args=(str(path), i, q)) for i in range(n_procs)]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(timeout=30)
        assert proc.exitcode == 0, proc.exitcode
    markers = [q.get() for _ in range(n_procs * n_each)]
    body = path.read_text(encoding="utf-8") if path.is_file() else ""
    return markers, body


@pytest.mark.offline
def test_ac4_threading_lock_does_not_serialise_processes(tmp_path: Path) -> None:
    """Rejected mechanism: per-process threading.Lock loses lines (AC4)."""
    dest = tmp_path / "threading.md"
    dest.write_text("", encoding="utf-8")
    markers, body = _run(_worker_threading_lock, dest, N_PROCS, N_ROUNDS)
    silent = [m for m in markers if m not in body]
    print(
        f"AC4_THREADING expected={EXPECTED} observed={body.count(chr(10))} "
        f"SILENT_LOSS={len(silent)}"
    )
    assert len(silent) > 0, "threading.Lock unexpectedly held across processes"


@pytest.mark.offline
def test_ac4_flock_serialises_processes(tmp_path: Path) -> None:
    dest = tmp_path / "flock.md"
    dest.write_text("", encoding="utf-8")
    markers, body = _run(_worker_flock_rmw, dest, N_PROCS, N_ROUNDS)
    silent = [m for m in markers if m not in body]
    print(
        f"AC4_FLOCK expected={EXPECTED} observed={body.count(chr(10))} "
        f"SILENT_LOSS={len(silent)}"
    )
    assert silent == []
    assert body.count("\n") == EXPECTED
    for marker in markers:
        assert marker in body


@pytest.mark.offline
def test_ac5_two_processes_lose_nothing(tmp_path: Path) -> None:
    dest = tmp_path / "two-proc.md"
    dest.write_text("", encoding="utf-8")
    markers, body = _run(_worker_two_proc, dest, 2, 8)
    silent = [m for m in markers if m not in body]
    print(
        f"AC5_TWO_PROC expected=16 observed={body.count(chr(10))} "
        f"SILENT_LOSS={len(silent)}"
    )
    assert silent == []
    assert body.count("\n") == 16


@pytest.mark.offline
def test_s1_named_modules_have_no_bare_write_text() -> None:
    libs_root = Path(__file__).resolve().parents[1]
    rels = (
        "cortex_store/dispatch_ops/_thread_sidecar.py",
        "cortex_store/dispatch_ops/_pinned_deliverable.py",
        "cortex_store/dispatch_ops/_recon_sidecar.py",
    )
    for rel in rels:
        text = (libs_root / rel).read_text(encoding="utf-8")
        assert ".write_text(" not in text, rel

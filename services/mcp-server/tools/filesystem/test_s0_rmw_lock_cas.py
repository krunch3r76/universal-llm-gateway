"""S0 probes for MCP RMW silent-loss: lock across edit + auto pre-image CAS."""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from unittest.mock import patch

import pytest

from tools._durable_write import PreImageMismatchError
from tools.file_editor import perform_edit
from tools.filesystem import _ops_text as ops_text
from tools.filesystem import _ops_write as ops_write
from tools.filesystem import _paths as paths

_LOUD_REASONS = frozenset({"file_sha256.mismatch", "write_verify_failed"})


@pytest.fixture
def sandbox_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "files"
    root.mkdir()
    monkeypatch.setattr(paths, "SANDBOX_ROOT", root)
    return root


def _silence_records(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ops_text, "record", lambda *_a, **_k: None)
    monkeypatch.setattr(ops_write, "record", lambda *_a, **_k: None)


def test_edit_file_impl_holds_path_write_lock_across_perform_edit(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC1: lock is held for the whole RMW, not only around the write."""
    _silence_records(monkeypatch)
    rel = "notes/system/threads/s0-lock.md"
    dest = sandbox_root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("head\n", encoding="utf-8")

    held = {"during_perform_edit": False}
    real = ops_text.perform_edit

    def _wrapped(*args: object, **kwargs: object) -> dict:
        key = str(dest.resolve())
        lock = paths._PATH_LOCKS.get(key)
        held["during_perform_edit"] = lock is not None and lock.locked()
        return real(*args, **kwargs)

    monkeypatch.setattr(ops_text, "perform_edit", _wrapped)
    result = ops_text.edit_file_impl(rel, "append", "tail\n")
    assert result["status"] == "edited: append"
    assert held["during_perform_edit"] is True
    assert dest.read_text(encoding="utf-8") == "head\ntail\n"


def test_edit_file_impl_and_write_file_impl_do_not_deadlock(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same path_write_lock; neither path re-enters it on this thread."""
    _silence_records(monkeypatch)
    rel = "notes/system/threads/s0-deadlock.md"
    ops_text.write_file_impl(rel, "seed\n")
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def _write() -> None:
        barrier.wait(timeout=5)
        ops_text.write_file_impl(rel, "from-write\n")

    def _edit() -> None:
        barrier.wait(timeout=5)
        ops_text.edit_file_impl(rel, "append", "from-edit\n")

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_write), pool.submit(_edit)]
        for future in as_completed(futures, timeout=10):
            exc = future.exception()
            if exc is not None:
                errors.append(exc)
    assert errors == []
    body = (sandbox_root / rel).read_text(encoding="utf-8")
    assert "from-write" in body or "from-edit" in body


def test_caller_expected_sha256_stays_optional_on_unclassified(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC2: explicit expected_sha256 is not made mandatory."""
    _silence_records(monkeypatch)
    rel = "notes/system/threads/s0-optional-cas.md"
    ops_text.write_file_impl(rel, "base\n")
    result = ops_text.edit_file_impl(rel, "append", "more\n")
    assert result["status"] == "edited: append"
    assert (sandbox_root / rel).read_text(encoding="utf-8") == "base\nmore\n"


def test_perform_edit_auto_cas_fails_loud_and_preserves_peer(
    tmp_path: Path,
) -> None:
    """AC3: pre-image mismatch is typed and does not clobber the peer."""
    target = tmp_path / "note.md"
    target.write_text("base\n", encoding="utf-8")
    real_read = Path.read_bytes

    def _read_then_clobber(self: Path) -> bytes:
        data = real_read(self)
        if self == target:
            target.write_text("peer-o-append\n", encoding="utf-8")
        return data

    with patch.object(Path, "read_bytes", _read_then_clobber):
        with pytest.raises(PreImageMismatchError) as exc_info:
            perform_edit(target, "append", "rmw-tail\n")
    assert exc_info.value.reason == "file_sha256.mismatch"
    assert target.read_text(encoding="utf-8") == "peer-o-append\n"


def test_concurrent_8x3_mcp_append_no_silent_loss(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G1 §6 S0 probe 1: 8 writers × 3 rounds through edit_file_impl append."""
    _silence_records(monkeypatch)
    rel = "notes/system/threads/s0-8x3.md"
    ops_text.write_file_impl(rel, "")
    dest = sandbox_root / rel
    n_writers, n_rounds = 8, 3
    barrier = threading.Barrier(n_writers)
    outcomes: list[tuple[str, dict]] = []
    lock = threading.Lock()

    def _worker(wid: int) -> None:
        barrier.wait(timeout=5)
        for round_i in range(n_rounds):
            marker = f"W{wid}R{round_i}\n"
            result = ops_text.edit_file_impl(rel, "append", marker)
            with lock:
                outcomes.append((marker, result))

    with ThreadPoolExecutor(max_workers=n_writers) as pool:
        futures = [pool.submit(_worker, i) for i in range(n_writers)]
        for future in as_completed(futures, timeout=30):
            future.result()

    body = dest.read_text(encoding="utf-8")
    silent_loss = [
        marker
        for marker, result in outcomes
        if str(result.get("status", "")).startswith("edited") and marker not in body
    ]
    loud = [
        result.get("reason")
        for _, result in outcomes
        if result.get("reason") in _LOUD_REASONS
    ]
    print(
        f"SILENT_LOSS={len(silent_loss)} expected=24 observed={body.count(chr(10))} "
        f"loud={len(loud)} reasons={sorted(set(loud))}"
    )
    assert silent_loss == [], silent_loss
    assert len(outcomes) == 24
    # Lock serializes in-process MCP writers: all 24 must land.
    assert body.count("\n") == 24
    for marker, result in outcomes:
        assert result.get("status") == "edited: append"
        assert marker in body


def test_mixed_o_append_rmw_loud_or_no_drop(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G1 §6 S0 probe 3: mixed O_APPEND × RMW — never success-and-drop."""
    _silence_records(monkeypatch)
    rel = "notes/system/threads/s0-mixed.md"
    ops_text.write_file_impl(rel, "seed\n")
    dest = sandbox_root / rel
    n_each = 8
    barrier = threading.Barrier(n_each * 2)
    rmw_results: list[tuple[str, dict]] = []
    oa_markers = [f"OA{i}\n" for i in range(n_each)]
    rmw_markers = [f"RMW{i}\n" for i in range(n_each)]
    lock = threading.Lock()

    def _o_append(marker: str) -> None:
        barrier.wait(timeout=5)
        fd = os.open(dest, os.O_WRONLY | os.O_APPEND)
        try:
            os.write(fd, marker.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

    def _rmw(marker: str) -> None:
        barrier.wait(timeout=5)
        result = ops_text.edit_file_impl(rel, "append", marker)
        with lock:
            rmw_results.append((marker, result))

    with ThreadPoolExecutor(max_workers=n_each * 2) as pool:
        futures = [pool.submit(_o_append, m) for m in oa_markers]
        futures.extend(pool.submit(_rmw, m) for m in rmw_markers)
        for future in as_completed(futures, timeout=30):
            future.result()

    body = dest.read_text(encoding="utf-8")
    rmw_success_missing = [
        marker
        for marker, result in rmw_results
        if str(result.get("status", "")).startswith("edited") and marker not in body
    ]
    oa_missing = [m for m in oa_markers if m not in body]
    rmw_loud = [
        result for _, result in rmw_results if result.get("reason") in _LOUD_REASONS
    ]
    rmw_success = [
        marker
        for marker, result in rmw_results
        if str(result.get("status", "")).startswith("edited")
    ]
    print(
        f"mixed SILENT_LOSS rmw_success_missing={len(rmw_success_missing)} "
        f"oa_missing={len(oa_missing)} rmw_loud={len(rmw_loud)} "
        f"rmw_success={len(rmw_success)} body_lines={body.count(chr(10))}"
    )
    assert rmw_success_missing == [], rmw_success_missing
    if oa_missing:
        assert rmw_loud, (
            f"O_APPEND lines dropped while every RMW reported success: "
            f"oa_missing={oa_missing} body={body!r}"
        )
    for marker in rmw_success:
        assert marker in body

"""CAS write guards for cortex fs write (friction 13695 P0-A)."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

from tools.filesystem import _ops_text as ops_text
from tools.filesystem import _paths as paths


@pytest.fixture
def sandbox_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "files"
    root.mkdir()
    monkeypatch.setattr(paths, "SANDBOX_ROOT", root)
    return root


def test_stale_expected_sha256_rejects_and_preserves_bytes(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_text, "record", lambda *_args, **_kwargs: None)
    rel = "notes/spec.md"
    first = ops_text.write_file_impl(rel, "version-one")
    assert first["status"] == "written"
    assert paths.sha256_of_file(sandbox_root / rel) is not None

    ops_text.write_file_impl(rel, "version-two")
    current = paths.sha256_of_file(sandbox_root / rel)
    stale = ops_text.write_file_impl(
        rel,
        "version-three",
        expected_sha256="sha256:0000000000000000000000000000000000000000000000000000000000000000",
    )
    assert stale["reason"] == "file_sha256.mismatch"
    assert stale["expected_sha256"].startswith("sha256:")
    assert stale["actual_sha256"] == current
    assert (sandbox_root / rel).read_text(encoding="utf-8") == "version-two"


def test_matching_expected_sha256_overwrites(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_text, "record", lambda *_args, **_kwargs: None)
    rel = "notes/spec.md"
    ops_text.write_file_impl(rel, "before")
    current = paths.sha256_of_file(sandbox_root / rel)
    result = ops_text.write_file_impl(rel, "after", expected_sha256=current)
    assert result["status"] == "written"
    assert (sandbox_root / rel).read_text(encoding="utf-8") == "after"


def test_if_absent_on_existing_rejects(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_text, "record", lambda *_args, **_kwargs: None)
    rel = "notes/new.md"
    ops_text.write_file_impl(rel, "original")
    rejected = ops_text.write_file_impl(rel, "clobber", if_absent=True)
    assert rejected["reason"] == "file_exists"
    assert (sandbox_root / rel).read_text(encoding="utf-8") == "original"


def test_if_absent_on_missing_writes(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_text, "record", lambda *_args, **_kwargs: None)
    rel = "notes/fresh.md"
    result = ops_text.write_file_impl(rel, "created", if_absent=True)
    assert result["status"] == "written"
    assert (sandbox_root / rel).read_text(encoding="utf-8") == "created"


def test_mutually_exclusive_guard_params_raise() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        ops_text.write_file_impl(
            "notes/x.md",
            "body",
            expected_sha256="sha256:abc",
            if_absent=True,
        )


def test_concurrent_if_absent_race_has_single_winner(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_text, "record", lambda *_args, **_kwargs: None)
    rel = "notes/race.md"
    barrier = threading.Barrier(2)
    results: list[dict] = []

    def _attempt(label: str) -> dict:
        barrier.wait(timeout=5)
        return ops_text.write_file_impl(rel, label, if_absent=True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_attempt, label) for label in ("winner-a", "winner-b")]
        for future in as_completed(futures):
            results.append(future.result())

    statuses = sorted(result.get("status", result.get("reason")) for result in results)
    assert statuses == ["file_exists", "written"]
    final = (sandbox_root / rel).read_text(encoding="utf-8")
    assert final in {"winner-a", "winner-b"}

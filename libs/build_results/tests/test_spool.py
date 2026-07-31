"""Tests for the build_results shared spool primitive."""

from __future__ import annotations

import json
import time
from pathlib import Path

import build_results.spool as spool
from build_results import compute_signals, result_ref, write_spool


def _envelope(dispatch_id: str = "abc123") -> dict:
    return {
        "dispatch_id": dispatch_id,
        "status": "failed",
        "stdout": "line1\nFAILED tests/test_x.py::test_y\nline3\n" + ("x" * 200_000),
        "stderr": "boom\n",
        "exit_code": 1,
        "duration_s": 2.5,
        "sidecar_path": None,
        "metadata": {
            "reason_code": "grok_nonzero_exit",
            "read_only_violation": False,
            "audit_incomplete": False,
            "git_diff_stat": " a | 2 +-",
        },
    }


def test_compute_signals_is_bounded() -> None:
    sig = compute_signals(_envelope())
    blob = json.dumps(sig)
    assert len(blob.encode()) < 128 * 1024
    assert sig["exit_code"] == 1
    assert sig["status"] == "failed"
    assert any("FAILED" in ln for ln in sig["failure_lines"])
    assert sig["failure_count"] >= 1


def test_result_ref_relative_paths() -> None:
    ref = result_ref("abc123")
    assert ref["sandbox"] == "workspaces"
    assert ref["signals_path"] == "ulg-build-results/abc123/signals.json"
    assert ref["sidecar_path"] == "ulg-build-results/abc123/sidecar.ndjson"
    assert not ref["result_dir"].startswith("/")  # relative, not absolute


def test_write_spool_writes_three_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(spool, "BUILD_RESULTS_DIR", tmp_path / "ulg-build-results")
    sidecar = tmp_path / "side.ndjson"
    sidecar.write_text('{"phase":"started"}\n', encoding="utf-8")
    sig = write_spool("abc123", _envelope(), sidecar_src=sidecar)
    d = tmp_path / "ulg-build-results" / "abc123"
    assert (d / "signals.json").exists()
    assert (d / "envelope.json").exists()
    assert (d / "sidecar.ndjson").read_text().startswith('{"phase":"started"}')
    assert sig["status"] == "failed"


def test_prune_removes_aged_dirs(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "ulg-build-results"
    monkeypatch.setattr(spool, "BUILD_RESULTS_DIR", root)
    old = root / "old"
    fresh = root / "fresh"
    old.mkdir(parents=True)
    fresh.mkdir(parents=True)
    old_time = time.time() - (8 * 24 * 60 * 60)
    import os

    os.utime(old, (old_time, old_time))
    removed = spool.prune_spool(retention_seconds=7 * 24 * 60 * 60)
    assert removed == 1
    assert not old.exists()
    assert fresh.exists()

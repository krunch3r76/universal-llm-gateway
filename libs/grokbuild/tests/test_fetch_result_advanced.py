"""fetch_result tests — incomplete/expired/truncated/V1 surface paths."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from grokbuild.fetch_result import fetch_result_op

from ._fetch_result_helpers import completed_records, now_ms, write_sidecar


@pytest.mark.asyncio
async def test_fetch_result_non_terminal_sidecar_without_registry_is_incomplete(
    sidecar_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("grokbuild.registry.REGISTRY_PATH", tmp_path / "r.json")
    write_sidecar(
        sidecar_root,
        "fetch-incomplete",
        [
            {
                "phase": "started",
                "ts": now_ms(),
                "cwd": str(tmp_path / "repo"),
                "mode": "read_only",
                "permission_mode": "plan",
            }
        ],
    )

    out = await fetch_result_op(dispatch_id="fetch-incomplete")

    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "sidecar_incomplete"
    assert out["metadata"]["http_status"] == 422


@pytest.mark.asyncio
async def test_fetch_result_retention_expired_returns_410(
    sidecar_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("grokbuild.fetch_result._RETENTION_SECONDS", 10)
    old_ts = int((time.time() - 60) * 1000)
    records = completed_records()
    records[0]["ts"] = old_ts - 1000
    records[-1]["ts"] = old_ts
    write_sidecar(sidecar_root, "fetch-expired", records)

    out = await fetch_result_op(dispatch_id="fetch-expired")

    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "result_retention_expired"
    assert out["metadata"]["http_status"] == 410
    assert out["metadata"]["retention_seconds"] == 10


@pytest.mark.asyncio
async def test_fetch_result_includes_truncated_chunks_in_stdout(
    sidecar_root: Path,
) -> None:
    """Truncated stdout/stderr chunks are included in reconstructed output.

    Review C2: the decode filters previously matched only "stdout_chunk" and
    "stderr_chunk", silently dropping over-cap lines persisted as
    "stdout_chunk_truncated" / "stderr_chunk_truncated". Reconstructed stdout
    was missing data and the recomputed truncated flag could flip True→False.
    Persisting `truncated` on the exit record makes the verdict decode-stable.
    """
    import json as _json

    dispatch_id = "truncated-test-id"
    sidecar = sidecar_root / f"{dispatch_id}.ndjson"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    ts = now_ms()
    sidecar.write_text(
        _json.dumps(
            {
                "phase": "started",
                "ts": ts - 2000,
                "cwd": "/tmp/r",
                "mode": "read_only",
                "permission_mode": "plan",
                "model": "",
                "session_id": None,
                "output_format": "streaming-json",
                "git_status_pre": "",
                "dirty_admission": False,
                "argv": [],
            }
        )
        + "\n"
        + _json.dumps(
            {
                "phase": "stdout_chunk",
                "ts": ts - 1500,
                "data": "first-line",
            }
        )
        + "\n"
        + _json.dumps(
            {
                "phase": "stdout_chunk_truncated",
                "ts": ts - 1000,
                "len": 100_000,
                "kept": 32 * 1024,
                "data": "x" * (32 * 1024),
            }
        )
        + "\n"
        + _json.dumps(
            {
                "phase": "stderr_chunk_truncated",
                "ts": ts - 500,
                "len": 1_000_000,
                "kept": 256 * 1024,
                "data": "e" * 100,
            }
        )
        + "\n"
        + _json.dumps(
            {
                "phase": "exit",
                "ts": ts,
                "status": "completed",
                "exit_code": 0,
                "duration_s": 2.0,
                "git_status_post": "",
                "git_diff_stat": "",
                "audit_incomplete": False,
                "sidecar_gaps": 0,
                "truncated": True,
            }
        )
        + "\n"
    )
    out = await fetch_result_op(dispatch_id=dispatch_id)
    assert out["status"] == "completed"
    assert "first-line" in out["stdout"]
    assert "x" * 100 in out["stdout"]
    assert "e" * 100 in out["stderr"]
    assert out["metadata"]["truncated"] is True


@pytest.mark.asyncio
async def test_fetch_result_reconstructs_v1_surface(
    sidecar_root: Path,
) -> None:
    import json as _json

    dispatch_id = "v1-test-id"
    sidecar = sidecar_root / f"{dispatch_id}.ndjson"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    ts = now_ms()
    sidecar.write_text(
        _json.dumps(
            {
                "phase": "started",
                "ts": ts - 4000,
                "cwd": "/tmp/r",
                "mode": "edit",
                "permission_mode": "acceptEdits",
                "model": "grok-4-fast",
                "session_id": "sid-X",
                "resume_strict": True,
                "tier": "balanced",
                "reasoning_effort": "medium",
                "effort": "medium",
                "check": True,
                "no_subagents": False,
                "disable_web_search": True,
                "max_turns": 5,
                "best_of_n": 3,
                "output_format": "streaming-json",
                "git_status_pre": "",
                "dirty_admission": False,
                "argv": [],
            }
        )
        + "\n"
        + _json.dumps(
            {
                "phase": "exit",
                "ts": ts,
                "status": "completed",
                "exit_code": 0,
                "duration_s": 4.0,
                "git_status_post": "",
                "git_diff_stat": "",
                "audit_incomplete": False,
                "sidecar_gaps": 0,
                "resolved_session_id": "sid-X",
                "reason_code": "",
            }
        )
        + "\n"
    )
    out = await fetch_result_op(dispatch_id=dispatch_id)
    assert out["status"] == "completed"
    meta = out["metadata"]
    assert meta["tier"] == "balanced"
    assert meta["reasoning_effort"] == "medium"
    assert meta["effort"] == "medium"
    assert meta["check"] is True
    assert meta["disable_web_search"] is True
    assert meta["resume_strict"] is True
    assert meta["resolved_session_id"] == "sid-X"
    assert meta["max_turns"] == 5
    assert meta["best_of_n"] == 3
    assert "continue_recent" not in meta

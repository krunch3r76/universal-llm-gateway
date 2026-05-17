"""fetch_result tests for grok_build sidecar retrieval."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from tools.grok_build import grok_build


def _now_ms() -> int:
    return int(time.time() * 1000)


def _write_sidecar(
    sidecar_root: Path,
    dispatch_id: str,
    records: list[dict[str, Any]],
) -> None:
    sidecar_root.mkdir(parents=True, exist_ok=True)
    path = sidecar_root / f"{dispatch_id}.ndjson"
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )


def _completed_records(
    *,
    cwd: str = "/tmp/repo",
    dispatch_id: str = "fetch-ok",
    exit_code: int | None = 0,
    status: str = "completed",
) -> list[dict[str, Any]]:
    start = _now_ms() - 1000
    return [
        {
            "phase": "started",
            "ts": start,
            "argv": [
                "/usr/bin/grok",
                "-p",
                "prompt",
                "--cwd",
                cwd,
                "--output-format",
                "json",
                "--permission-mode",
                "plan",
                "--always-approve",
            ],
            "cwd": cwd,
            "mode": "read_only",
            "permission_mode": "plan",
            "model": "grok-4.3",
            "session_id": None,
            "continue_recent": False,
            "output_format": "json",
            "git_status_pre": "",
            "dirty_admission": False,
        },
        {"phase": "stdout_chunk", "ts": start + 200, "data": "hello"},
        {"phase": "stderr_chunk", "ts": start + 300, "data": "warning\n"},
        {
            "phase": "exit",
            "ts": start + 1000,
            "status": status,
            "exit_code": exit_code,
            "duration_s": 1.0,
            "git_status_post": "",
            "git_diff_stat": "",
            "audit_incomplete": False,
            "sidecar_gaps": 0,
        },
    ]


@pytest.mark.asyncio
async def test_fetch_result_json_reconstructs_dispatch_envelope(
    sidecar_root: Path,
) -> None:
    _write_sidecar(sidecar_root, "fetch-ok", _completed_records(cwd="/tmp/repo"))

    out = await grok_build(op="fetch_result", dispatch_id="fetch-ok", format="json")

    assert out["status"] == "completed"
    assert out["stdout"] == "hello"
    assert out["stderr"] == "warning\n"
    assert out["exit_code"] == 0
    assert out["metadata"]["cwd"] == "/tmp/repo"
    assert out["metadata"]["mode"] == "read_only"
    assert out["metadata"]["model"] == "grok-4.3"
    assert out["metadata"]["format"] == "json"
    assert out["metadata"]["record_count"] == 4
    assert out["metadata"]["http_status"] == 200
    assert len(out["records"]) == 4


@pytest.mark.asyncio
async def test_fetch_result_text_and_summary_formats_are_distinct(
    sidecar_root: Path,
) -> None:
    records = _completed_records(
        cwd="/tmp/repo",
        dispatch_id="fetch-failed",
        exit_code=2,
        status="failed",
    )
    _write_sidecar(sidecar_root, "fetch-failed", records)

    text = await grok_build(op="fetch_result", dispatch_id="fetch-failed", format="text")
    summary = await grok_build(
        op="fetch_result", dispatch_id="fetch-failed", format="summary"
    )

    assert text["status"] == "failed"
    assert text["metadata"]["format"] == "text"
    assert "text" in text
    assert "records" not in text
    assert "status: failed" in text["text"]
    assert summary["metadata"]["format"] == "summary"
    assert "summary" in summary
    assert "records" not in summary
    assert summary["summary"]["exit_code"] == 2
    assert summary["summary"]["stderr_preview"] == "warning\n"


@pytest.mark.asyncio
async def test_fetch_result_missing_sidecar_returns_404(
    sidecar_root: Path,
) -> None:
    sidecar_root.mkdir(parents=True, exist_ok=True)

    out = await grok_build(op="fetch_result", dispatch_id="missing-id")

    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "result_not_found"
    assert out["metadata"]["http_status"] == 404


@pytest.mark.asyncio
async def test_fetch_result_rejects_invalid_dispatch_id() -> None:
    out = await grok_build(op="fetch_result", dispatch_id="../escape")

    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "invalid_dispatch_id"
    assert out["metadata"]["http_status"] == 400


@pytest.mark.asyncio
async def test_fetch_result_uses_registry_for_in_flight_detection(
    sidecar_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools._grok_build_registry import _reset_for_tests, try_acquire_cwd

    registry_path = tmp_path / "registry.json"
    monkeypatch.setattr("tools._grok_build_registry.REGISTRY_PATH", registry_path)
    _reset_for_tests()
    cwd = str(tmp_path / "repo")
    Path(cwd).mkdir()
    _write_sidecar(
        sidecar_root,
        "fetch-live",
        [
            {
                "phase": "started",
                "ts": _now_ms(),
                "cwd": cwd,
                "mode": "read_only",
                "permission_mode": "plan",
            }
        ],
    )
    assert await try_acquire_cwd(cwd) is True

    out = await grok_build(op="fetch_result", dispatch_id="fetch-live")

    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "dispatch_in_flight"
    assert out["metadata"]["http_status"] == 409
    _reset_for_tests()


@pytest.mark.asyncio
async def test_fetch_result_non_terminal_sidecar_without_registry_is_incomplete(
    sidecar_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tools._grok_build_registry.REGISTRY_PATH", tmp_path / "r.json")
    _write_sidecar(
        sidecar_root,
        "fetch-incomplete",
        [
            {
                "phase": "started",
                "ts": _now_ms(),
                "cwd": str(tmp_path / "repo"),
                "mode": "read_only",
                "permission_mode": "plan",
            }
        ],
    )

    out = await grok_build(op="fetch_result", dispatch_id="fetch-incomplete")

    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "sidecar_incomplete"
    assert out["metadata"]["http_status"] == 422


@pytest.mark.asyncio
async def test_fetch_result_retention_expired_returns_410(
    sidecar_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tools._grok_build_fetch_result._RETENTION_SECONDS", 10)
    old_ts = int((time.time() - 60) * 1000)
    records = _completed_records()
    records[0]["ts"] = old_ts - 1000
    records[-1]["ts"] = old_ts
    _write_sidecar(sidecar_root, "fetch-expired", records)

    out = await grok_build(op="fetch_result", dispatch_id="fetch-expired")

    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "result_retention_expired"
    assert out["metadata"]["http_status"] == 410
    assert out["metadata"]["retention_seconds"] == 10

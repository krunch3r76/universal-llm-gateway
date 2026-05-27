"""fetch_result tests — basic format/status/error paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from grokbuild.fetch_result import fetch_result_op

from ._fetch_result_helpers import completed_records, write_sidecar


@pytest.mark.asyncio
async def test_fetch_result_json_reconstructs_dispatch_envelope(
    sidecar_root: Path,
) -> None:
    write_sidecar(sidecar_root, "fetch-ok", completed_records(cwd="/tmp/repo"))

    out = await fetch_result_op(dispatch_id="fetch-ok", format="json")

    assert out["status"] == "completed"
    assert out["stdout"] == "hello"
    assert out["stderr"] == "warning\n"
    assert out["exit_code"] == 0
    assert out["metadata"]["cwd"] == "/tmp/repo"
    assert out["metadata"]["mode"] == "read_only"
    assert out["metadata"]["model"] == "grok-build"
    assert out["metadata"]["format"] == "json"
    assert out["metadata"]["record_count"] == 4
    assert out["metadata"]["http_status"] == 200
    assert len(out["records"]) == 4


@pytest.mark.asyncio
async def test_fetch_result_text_and_summary_formats_are_distinct(
    sidecar_root: Path,
) -> None:
    records = completed_records(
        cwd="/tmp/repo",
        dispatch_id="fetch-failed",
        exit_code=2,
        status="failed",
    )
    write_sidecar(sidecar_root, "fetch-failed", records)

    text = await fetch_result_op(dispatch_id="fetch-failed", format="text")
    summary = await fetch_result_op(dispatch_id="fetch-failed", format="summary")

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

    out = await fetch_result_op(dispatch_id="missing-id")

    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "result_not_found"
    assert out["metadata"]["http_status"] == 404


@pytest.mark.asyncio
async def test_fetch_result_rejects_invalid_dispatch_id() -> None:
    out = await fetch_result_op(dispatch_id="../escape")

    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "invalid_dispatch_id"
    assert out["metadata"]["http_status"] == 400


@pytest.mark.asyncio
async def test_fetch_result_uses_registry_for_in_flight_detection(
    sidecar_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from grokbuild.registry import _reset_for_tests, try_acquire_cwd

    registry_path = tmp_path / "registry.json"
    monkeypatch.setattr("grokbuild.registry.REGISTRY_PATH", registry_path)
    _reset_for_tests()
    cwd = str(tmp_path / "repo")
    Path(cwd).mkdir()
    from ._fetch_result_helpers import now_ms

    write_sidecar(
        sidecar_root,
        "fetch-live",
        [
            {
                "phase": "started",
                "ts": now_ms(),
                "cwd": cwd,
                "mode": "read_only",
                "permission_mode": "plan",
            }
        ],
    )
    assert await try_acquire_cwd(cwd) is True

    out = await fetch_result_op(dispatch_id="fetch-live")

    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "dispatch_in_flight"
    assert out["metadata"]["http_status"] == 409
    _reset_for_tests()

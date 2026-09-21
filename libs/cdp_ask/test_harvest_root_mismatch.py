"""Hermetic tests for harvest_root_mismatch fail-closed (a:36049)."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from cdp_ask import runner
from cdp_ask.app import create_app
from cdp_ask.execution_store import ExecutionStore
from cdp_ask.runner import (
    HarvestRootMismatchError,
    _load_prompt_uri,
    verify_harvest_root,
)

pytestmark = pytest.mark.offline


def _pin_tmp_harvest_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(runner, "_TMP_FALLBACK_HARVEST_ROOT", tmp_path)
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    return tmp_path


def test_verify_harvest_root_raises_when_tmp_pinned_and_nfs_live(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _pin_tmp_harvest_root(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "_nfs_cortex_files_accessible", lambda: True)
    with pytest.raises(HarvestRootMismatchError, match="harvest_root_mismatch"):
        verify_harvest_root()
    assert root.is_dir()


def test_verify_harvest_root_allows_tmp_when_nfs_unreachable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _pin_tmp_harvest_root(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "_nfs_cortex_files_accessible", lambda: False)
    assert verify_harvest_root() == root.resolve()


def test_load_prompt_uri_missing_on_tmp_raises_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _pin_tmp_harvest_root(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "_nfs_cortex_files_accessible", lambda: False)
    with pytest.raises(HarvestRootMismatchError, match="harvest_root_mismatch"):
        _load_prompt_uri("cortex://notes/system/missing-prompt.md")


def test_load_prompt_uri_missing_on_live_root_raises_value_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    live_root = tmp_path / "live-files"
    live_root.mkdir()
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(live_root))
    with pytest.raises(ValueError, match="prompt_uri not found"):
        _load_prompt_uri("cortex://notes/system/missing-prompt.md")


def test_nfs_probe_uses_timeout_test_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc = MagicMock(returncode=0)
    run = MagicMock(return_value=proc)
    monkeypatch.setattr(runner.subprocess, "run", run)
    assert runner._nfs_cortex_files_accessible() is True
    run.assert_called_once_with(
        ["timeout", "2", "test", "-d", "/mnt/torus/mcp-data/files"],
        check=False,
        capture_output=True,
        timeout=3,
    )


def test_submit_marks_failed_on_harvest_root_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _pin_tmp_harvest_root(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "_nfs_cortex_files_accessible", lambda: False)
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.list_active",
        lambda: [],
    )
    monkeypatch.setattr(
        "claude_bundles.cdp_orphans.probe_live_ports",
        lambda port_range=None: [],
    )
    store = ExecutionStore()
    app = create_app(store=store)
    with TestClient(app) as client:
        resp = client.post(
            "/v1/project-ask/executions",
            json={
                "prompt_uri": "cortex://notes/system/missing-prompt.md",
                "project_uuid": "proj-uuid",
            },
        )
        assert resp.status_code == 202
        execution_id = resp.json()["execution_id"]
        poll: dict[str, object] = {"status": "running"}
        for _ in range(100):
            poll = client.get(
                f"/v1/project-ask/executions/{execution_id}"
            ).json()
            if poll["status"] != "running":
                break
            time.sleep(0.02)
    assert poll["status"] == "failed"
    assert "harvest_root_mismatch" in str(poll.get("error") or "")
    assert poll.get("stall_stage") == "mark_terminal"
    assert poll.get("ok") is not True
    assert poll.get("body_len") in {0, None}

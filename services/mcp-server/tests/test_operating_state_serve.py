"""Unit tests for fleet operating-state MCP fs serve-hook."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from claude_bundles.what_is_running_view import (
    HONEST_EMPTY_SESSIONS,
    SNAPSHOT_URI,
    serve_view,
)
from tools import _file_helpers
from tools._file_helpers import read_file_result
from tools.filesystem import _paths as paths

pytestmark = pytest.mark.offline

_REL = "notes/system/operational/what-is-running.json"


@pytest.fixture
def sandbox_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "files"
    root.mkdir()
    monkeypatch.setattr(paths, "SANDBOX_ROOT", root)
    monkeypatch.setattr(_file_helpers, "FILES_ROOT", root)
    return root


def _disk_hex(file_path: Path) -> str:
    return hashlib.sha256(file_path.read_bytes()).hexdigest()


def _stale_snapshot() -> dict:
    return {
        "schema": "what-is-running/v1",
        "observed_at_utc": "2026-08-07T08:22:06.668103Z",
        "running": [
            {
                "execution_id": "exec-stale",
                "lane": "6885",
                "expires_at_utc": "2026-08-07T08:27:06.668103Z",
            }
        ],
        "intended": {"obligation": True, "expiring": False},
        "findings": [{"verdict": "MULTI_LANE_OK", "obligation": True, "expiring": False}],
    }


def test_operating_state_read_applies_serve_view(sandbox_root: Path) -> None:
    target = sandbox_root / _REL
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(_stale_snapshot(), indent=2) + "\n", encoding="utf-8")

    result = read_file_result(_REL, root=sandbox_root)
    served = json.loads(result["content"])

    assert result["serve_view_applied"] is True
    assert result["snapshot_uri"] == SNAPSHOT_URI
    assert result["read_sha256"] == _disk_hex(target)
    assert result["served_sha256"] == hashlib.sha256(
        result["content"].encode("utf-8")
    ).hexdigest()
    assert served["running"] == []
    assert served.get("liveness_assertion") == HONEST_EMPTY_SESSIONS
    assert served["intended"]["obligation"] is True
    assert served["findings"]


def test_operating_state_binary_returns_served_text_not_raw_base64(
    sandbox_root: Path,
) -> None:
    target = sandbox_root / _REL
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(_stale_snapshot(), indent=2) + "\n", encoding="utf-8")

    result = read_file_result(_REL, root=sandbox_root, binary=True)

    assert "content_base64" not in result
    assert result["binary_served_as_text"] is True
    served = json.loads(result["content"])
    assert served["running"] == []
    assert result["read_sha256"] == _disk_hex(target)


def test_operating_state_read_sha256_unchanged_when_content_served(
    sandbox_root: Path,
) -> None:
    target = sandbox_root / _REL
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(_stale_snapshot(), indent=2) + "\n", encoding="utf-8")

    result = read_file_result(_REL, root=sandbox_root)

    assert result["read_sha256"] == _disk_hex(target)
    assert result["served_sha256"] != result["read_sha256"]


def test_non_snapshot_read_unaffected(sandbox_root: Path) -> None:
    rel = "notes/other.json"
    target = sandbox_root / rel
    target.parent.mkdir(parents=True)
    target.write_text('{"ok": true}\n', encoding="utf-8")

    result = read_file_result(rel, root=sandbox_root)

    assert "serve_view_applied" not in result
    assert result["read_sha256"] == _disk_hex(target)
    assert json.loads(result["content"]) == {"ok": True}

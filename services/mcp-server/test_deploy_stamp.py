"""Deploy stamp reader — no container required."""

from __future__ import annotations

from pathlib import Path

import pytest

from _deploy_stamp import health_json, read_source_sync_stamp


def test_read_source_sync_stamp_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stamp = tmp_path / ".source_sync_stamp"
    monkeypatch.setattr("_deploy_stamp._STAMP_PATH", stamp)
    assert read_source_sync_stamp() == {
        "source_synced_at": None,
        "deploy_mode": "image_only",
    }


def test_read_source_sync_stamp_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stamp = tmp_path / ".source_sync_stamp"
    stamp.write_text("2026-06-02T21:30:00Z\n", encoding="utf-8")
    monkeypatch.setattr("_deploy_stamp._STAMP_PATH", stamp)
    assert read_source_sync_stamp() == {
        "source_synced_at": "2026-06-02T21:30:00Z",
        "deploy_mode": "source_synced",
    }


def test_health_json_includes_stamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stamp = tmp_path / ".source_sync_stamp"
    stamp.write_text("2026-06-02T21:30:00Z\n", encoding="utf-8")
    monkeypatch.setattr("_deploy_stamp._STAMP_PATH", stamp)
    payload = health_json()
    assert payload["status"] == "ok"
    assert payload["source_synced_at"] == "2026-06-02T21:30:00Z"
    assert payload["deploy_mode"] == "source_synced"

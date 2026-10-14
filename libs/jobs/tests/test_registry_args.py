"""Production argument validators and reviewed flag lists."""

from __future__ import annotations

import subprocess

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from jobs.registry import (
    BusReplyWatchArgs,
    ClaudeSyncArgs,
    IdeHopArgs,
    InstallEcosystemPluginArgs,
    _ide_argv,
    _install_plugin_argv,
    _sync_argv,
    _watch_argv,
)
from jobs.server import create_app
from jobs.tests.conftest import HEADERS, TOKEN


@pytest.fixture
def prod(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("JOBS_TOKEN", TOKEN)
    monkeypatch.setenv("JOBS_STATE_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        yield client


def _post(prod: TestClient, job: str, args: dict) -> object:
    return prod.post(f"/api/v1/jobs/{job}", headers=HEADERS, json={"args": args})


@pytest.mark.offline
def test_upload_slugs(prod: TestClient) -> None:
    empty = _post(prod, "claude-ai-sync", {"action": "upload", "slugs": []})
    slash = _post(prod, "claude-ai-sync", {"action": "upload", "slugs": ["Bad/Slug"]})
    upper = _post(prod, "claude-ai-sync", {"action": "upload", "slugs": ["Nope"]})
    assert empty.status_code == slash.status_code == upper.status_code == 422


@pytest.mark.offline
def test_connector_enum(prod: TestClient) -> None:
    response = _post(
        prod,
        "claude-ai-sync",
        {"action": "refresh_connector", "connector": "https://evil.example/mcp"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "args_invalid"


@pytest.mark.offline
def test_producer_declaration_xor(prod: TestClient) -> None:
    both = _post(
        prod,
        "bus-reply-watch",
        {
            "thread": "12",
            "after_turn": 1,
            "label": "gate1",
            "execution_id": "abc",
            "no_producer": True,
        },
    )
    neither = _post(
        prod,
        "bus-reply-watch",
        {"thread": "12", "after_turn": 1, "label": "gate1"},
    )
    assert both.status_code == 422 and neither.status_code == 422


@pytest.mark.offline
def test_production_flags_match_scripts() -> None:
    ide = _ide_argv(
        IdeHopArgs.model_validate(
            {
                "root": "12345",
                "row": "now",
                "transcript_id": "11111111-1111-1111-1111-111111111111",
                "dry_run": True,
            }
        )
    )
    assert ide[:2] == ["--root", "12345"]
    assert "--dry-run" in ide
    for banned in ("--gui-host", "--remote-repo", "--no-raise", "--force", "--find-transcript"):
        assert banned not in ide
    watch = _watch_argv(
        BusReplyWatchArgs.model_validate(
            {"thread": "12", "after_turn": 1, "label": "gate1", "no_producer": True, "page": True}
        )
    )
    assert "--page" in watch and "--no-page" not in watch
    off = _watch_argv(
        BusReplyWatchArgs.model_validate(
            {"thread": "12", "after_turn": 1, "label": "gate1", "no_producer": True, "page": False}
        )
    )
    assert "--page" not in off and "--no-page" not in off
    upload = _sync_argv(ClaudeSyncArgs.model_validate({"action": "upload", "slugs": ["demo"]}))
    assert upload[:2] == ["upload", "--slugs"]
    missing = subprocess.run(
        ["scripts/ingest-article", "--arxiv", "2401.00001"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert missing.returncode != 0
    dry = subprocess.run(
        ["scripts/ingest-article", "--download-only", "--arxiv", "2401.00001", "--dry-run"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert dry.returncode == 0


@pytest.mark.offline
def test_install_plugin_argv_defaults_dry_run() -> None:
    argv = _install_plugin_argv(InstallEcosystemPluginArgs())
    assert argv == ["--dry-run"]
    assert "--local-only" not in argv


@pytest.mark.offline
def test_install_plugin_dest_local_omits_dry_run() -> None:
    argv = _install_plugin_argv(InstallEcosystemPluginArgs(dest="local"))
    assert "--dry-run" not in argv
    assert argv == []


@pytest.mark.offline
def test_install_plugin_local_only_flag() -> None:
    dry = _install_plugin_argv(InstallEcosystemPluginArgs(local_only=True))
    assert dry == ["--dry-run", "--local-only"]
    live = _install_plugin_argv(InstallEcosystemPluginArgs(dest="local", local_only=True))
    assert live == ["--local-only"]
    assert "--dry-run" not in live


@pytest.mark.offline
def test_install_plugin_rejects_unknown_and_bad_dest() -> None:
    with pytest.raises(ValidationError):
        InstallEcosystemPluginArgs.model_validate({"home": "/tmp/evil"})
    with pytest.raises(ValidationError):
        InstallEcosystemPluginArgs.model_validate({"dest": "remote"})


@pytest.mark.offline
def test_install_plugin_http_rejects(prod: TestClient) -> None:
    unknown = _post(prod, "install-ecosystem-plugin", {"dest_path": "/tmp/x"})
    other = _post(prod, "install-ecosystem-plugin", {"dest": "hub"})
    assert unknown.status_code == 422
    assert other.status_code == 422

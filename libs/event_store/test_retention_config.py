"""Live ~/.gateway/event-service.yaml drives retention policy."""

from __future__ import annotations

from pathlib import Path

import pytest

from event_store.retention_config import load_retention_settings


@pytest.mark.offline
def test_load_retention_from_yaml_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "event-service.yaml"
    cfg.write_text(
        "retention:\n  days: 2\n  max_sessions: 3\n  automatic: run\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("EVENT_SERVICE_CONFIG_PATH", str(cfg))
    monkeypatch.delenv("EVENTS_RETENTION_STARTUP", raising=False)
    settings = load_retention_settings(cli_retention_days=7, cli_max_sessions=2)
    assert settings.retention_days == 2
    assert settings.max_sessions == 3
    assert settings.automatic == "run"


@pytest.mark.offline
def test_env_overrides_yaml_automatic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "event-service.yaml"
    cfg.write_text("retention:\n  automatic: run\n", encoding="utf-8")
    monkeypatch.setenv("EVENT_SERVICE_CONFIG_PATH", str(cfg))
    monkeypatch.setenv("EVENTS_RETENTION_STARTUP", "dry_run")
    settings = load_retention_settings()
    assert settings.automatic == "dry_run"


@pytest.mark.offline
def test_missing_yaml_defaults_to_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVENT_SERVICE_CONFIG_PATH", "/nonexistent/event-service.yaml")
    monkeypatch.delenv("EVENTS_RETENTION_STARTUP", raising=False)
    settings = load_retention_settings(cli_retention_days=9, cli_max_sessions=4)
    assert settings.retention_days == 9
    assert settings.max_sessions == 4
    assert settings.automatic == "dry_run"

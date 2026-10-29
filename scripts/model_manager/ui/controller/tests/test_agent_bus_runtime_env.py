"""Tests for agent-bus host runtime env wiring."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.model_manager.ui.controller.service_config import (
    AgentBusConfig,
    McpConfig,
    agent_bus_project_ask_url_for_runtime,
)
from scripts.model_manager.ui.controller.service_ctl import agent_bus_service


def test_agent_bus_project_ask_url_prefers_agent_bus_yaml() -> None:
    bus = AgentBusConfig(project_ask_url="http://jupiter:8770")
    mcp = McpConfig(auth_token="t", project_ask_url="http://other:8770")
    assert agent_bus_project_ask_url_for_runtime(bus, mcp) == "http://jupiter:8770"


def test_agent_bus_project_ask_url_falls_back_to_mcp_yaml() -> None:
    bus = AgentBusConfig()
    mcp = McpConfig(auth_token="t", project_ask_url="http://jupiter:8770")
    assert agent_bus_project_ask_url_for_runtime(bus, mcp) == "http://jupiter:8770"


def test_agent_bus_runtime_env_includes_project_ask_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "messages.db"
    monkeypatch.setattr(
        agent_bus_service,
        "load_agent_bus_config",
        lambda: AgentBusConfig(db_path=str(db_path), project_ask_url="http://jupiter:8770"),
    )
    monkeypatch.setattr(agent_bus_service, "ensure_agent_bus_config", lambda: tmp_path)
    monkeypatch.setattr(agent_bus_service, "load_mcp_config", lambda: None)

    env, error = agent_bus_service._agent_bus_runtime_env()

    assert error is None
    assert env.get("PROJECT_ASK_URL") == "http://jupiter:8770"

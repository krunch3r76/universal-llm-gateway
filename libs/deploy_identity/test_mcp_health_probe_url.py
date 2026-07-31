"""Unit tests for MCP health probe URL resolution."""

from __future__ import annotations

from pathlib import Path

from deploy_identity.mcp_health_probe_url import resolve_mcp_health_probe_url


def test_explicit_env_wins(monkeypatch):
    monkeypatch.setenv("MCP_HEALTH_URL", "https://example.test/custom/health")
    monkeypatch.setenv("MCP_PUBLIC_URL", "https://ignored.test/mcp/code/")
    assert resolve_mcp_health_probe_url() == "https://example.test/custom/health"


def test_derives_from_mcp_public_url(monkeypatch, tmp_path):
    monkeypatch.delenv("MCP_HEALTH_URL", raising=False)
    monkeypatch.setenv("MCP_PUBLIC_URL", "https://mcp.k-1.me/mcp/code/")
    monkeypatch.setenv("CHARTER_RUNNER_OPERATOR_HOME", str(tmp_path))
    assert resolve_mcp_health_probe_url() == "https://mcp.k-1.me/health"


def test_derives_from_mcp_server_url(monkeypatch, tmp_path):
    monkeypatch.delenv("MCP_HEALTH_URL", raising=False)
    monkeypatch.delenv("MCP_PUBLIC_URL", raising=False)
    monkeypatch.setenv("MCP_SERVER_URL", "https://mcp.k-1.me/mcp/life")
    monkeypatch.setenv("CHARTER_RUNNER_OPERATOR_HOME", str(tmp_path))
    assert resolve_mcp_health_probe_url() == "https://mcp.k-1.me/health"


def test_falls_back_to_gateway_yaml(monkeypatch, tmp_path):
    monkeypatch.delenv("MCP_HEALTH_URL", raising=False)
    monkeypatch.delenv("MCP_PUBLIC_URL", raising=False)
    monkeypatch.delenv("MCP_SERVER_URL", raising=False)
    gateway = tmp_path / ".gateway"
    gateway.mkdir()
    (gateway / "mcp.yaml").write_text(
        "mcp_server_url: https://mcp.k-1.me/mcp/code\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CHARTER_RUNNER_OPERATOR_HOME", str(tmp_path))
    assert resolve_mcp_health_probe_url() == "https://mcp.k-1.me/health"


def test_local_default_when_unconfigured(monkeypatch, tmp_path):
    monkeypatch.delenv("MCP_HEALTH_URL", raising=False)
    monkeypatch.delenv("MCP_PUBLIC_URL", raising=False)
    monkeypatch.delenv("MCP_SERVER_URL", raising=False)
    monkeypatch.setenv("CHARTER_RUNNER_OPERATOR_HOME", str(tmp_path))
    assert resolve_mcp_health_probe_url() == "http://127.0.0.1:8080/health"

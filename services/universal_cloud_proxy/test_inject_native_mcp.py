"""Unit tests for _inject_native_mcp xAI defensive guard.

Verifies that -mcp suffix requests for xAI are forwarded without injecting
a type:mcp tool entry (xAI does not yet support it), and that OpenAI still
receives the tool entry.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from services.universal_cloud_proxy import native_mcp_inject
from services.universal_cloud_proxy.native_mcp_inject import inject_native_mcp

_PATCH_EXECUTOR = "services.universal_cloud_proxy.cloud_proxy._get_mcp_executor"
_PATCH_CONFIG = (
    "services.universal_cloud_proxy.native_mcp_inject.get_mcp_config_for_provider"
)
_FAKE_CONFIG = {"url": "https://mcp.example.com/mcp", "token": "test-token"}


def _call_inject_with_config(provider_key: str, config: dict) -> dict:
    body: dict = {"model": "test-model"}
    with (
        patch(_PATCH_EXECUTOR, return_value=MagicMock()),
        patch(_PATCH_CONFIG, return_value=config),
    ):
        inject_native_mcp(provider_key, body)
    return body


def _call_inject(provider_key: str) -> dict:
    return _call_inject_with_config(provider_key, _FAKE_CONFIG)


def test_xai_mcp_injection_skipped() -> None:
    body = _call_inject("xai")
    assert "tools" not in body, "xAI must not receive a type:mcp tools entry"


def test_xai_mcp_skip_logs_info(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(
        logging.INFO, logger="services.universal_cloud_proxy.native_mcp_inject"
    ):
        _call_inject("xai")
    assert any(
        "xAI does not yet support type:mcp" in record.message
        for record in caplog.records
    ), "Expected INFO log for xAI MCP skip"


def test_openai_mcp_injection_succeeds() -> None:
    body = _call_inject("openai")
    assert "tools" in body
    assert len(body["tools"]) == 1
    entry = body["tools"][0]
    assert entry["type"] == "mcp"
    assert entry["server_label"] == "vortex"
    assert "Bearer" in entry["authorization"]


def test_anthropic_inject_normalizes_bare_mcp_url_to_code_mount(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    cfg = tmp_path / "mcp.yaml"
    cfg.write_text("auth_token: admin\nanthropic_callback_token: callback-tok\n")
    monkeypatch.setenv("MCP_CONFIG_PATH", str(cfg))
    body: dict = {"model": "claude-sonnet-4-6"}
    provider_url = "https://mcp.k-1.me/mcp"
    with (
        patch(_PATCH_EXECUTOR, return_value=MagicMock()),
        patch(_PATCH_CONFIG, return_value={"url": provider_url, "token": ""}),
    ):
        inject_native_mcp("anthropic", body)
    assert body["mcp_servers"][0]["url"] == "https://mcp.k-1.me/mcp/code/"


def test_anthropic_inject_bare_mcp_url_requires_normalize_mcp_public_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    """Mutation pair: without normalize the bare /mcp url is forwarded (404 mount)."""
    cfg = tmp_path / "mcp.yaml"
    cfg.write_text("auth_token: admin\nanthropic_callback_token: callback-tok\n")
    monkeypatch.setenv("MCP_CONFIG_PATH", str(cfg))
    provider_url = "https://mcp.k-1.me/mcp"
    body: dict = {"model": "claude-sonnet-4-6"}
    real_normalize = native_mcp_inject.normalize_mcp_public_url

    def passthrough(url: str) -> str:
        return url

    with (
        patch(_PATCH_EXECUTOR, return_value=MagicMock()),
        patch(_PATCH_CONFIG, return_value={"url": provider_url, "token": ""}),
        patch.object(native_mcp_inject, "normalize_mcp_public_url", passthrough),
    ):
        inject_native_mcp("anthropic", body)
    assert body["mcp_servers"][0]["url"] == provider_url

    body = {"model": "claude-sonnet-4-6"}
    with (
        patch(_PATCH_EXECUTOR, return_value=MagicMock()),
        patch(_PATCH_CONFIG, return_value={"url": provider_url, "token": ""}),
        patch.object(native_mcp_inject, "normalize_mcp_public_url", real_normalize),
    ):
        inject_native_mcp("anthropic", body)
    assert body["mcp_servers"][0]["url"] == "https://mcp.k-1.me/mcp/code/"


def test_anthropic_inject_pairs_toolset_and_uses_callback_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    cfg = tmp_path / "mcp.yaml"
    cfg.write_text("auth_token: admin\nanthropic_callback_token: callback-tok\n")
    monkeypatch.setenv("MCP_CONFIG_PATH", str(cfg))
    monkeypatch.delenv("MCP_ANTHROPIC_CALLBACK_TOKEN", raising=False)
    body: dict = {"model": "claude-sonnet-4-6"}
    with (
        patch(_PATCH_EXECUTOR, return_value=MagicMock()),
        patch(_PATCH_CONFIG, return_value={"url": _FAKE_CONFIG["url"], "token": ""}),
    ):
        inject_native_mcp("anthropic", body)
    assert body["mcp_servers"][0]["authorization_token"] == "callback-tok"
    assert {"type": "mcp_toolset", "mcp_server_name": "vortex"} in body["tools"]


def test_anthropic_inject_skipped_when_config_token_equals_callback_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    cfg = tmp_path / "mcp.yaml"
    cfg.write_text("auth_token: admin\nanthropic_callback_token: same-tok\n")
    monkeypatch.setenv("MCP_CONFIG_PATH", str(cfg))
    body = _call_inject_with_config(
        "anthropic", {"url": _FAKE_CONFIG["url"], "token": "same-tok"}
    )
    assert "mcp_servers" not in body


def test_openai_still_skips_when_config_token_empty() -> None:
    body = _call_inject_with_config("openai", {"url": _FAKE_CONFIG["url"], "token": ""})
    assert "tools" not in body

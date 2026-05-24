"""Unit tests for _inject_native_mcp xAI defensive guard.

Verifies that -mcp suffix requests for xAI are forwarded without injecting
a type:mcp tool entry (xAI does not yet support it), and that OpenAI still
receives the tool entry.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from services.universal_cloud_proxy.native_routes import _inject_native_mcp

_PATCH_EXECUTOR = "services.universal_cloud_proxy.cloud_proxy._get_mcp_executor"
_PATCH_CONFIG = (
    "services.universal_cloud_proxy.native_routes._get_mcp_config_for_provider"
)
_FAKE_CONFIG = {"url": "https://mcp.example.com/mcp", "token": "test-token"}


def _call_inject(provider_key: str) -> dict:
    body: dict = {"model": "test-model"}
    with (
        patch(_PATCH_EXECUTOR, return_value=MagicMock()),
        patch(_PATCH_CONFIG, return_value=_FAKE_CONFIG),
    ):
        _inject_native_mcp(provider_key, body)
    return body


def test_xai_mcp_injection_skipped() -> None:
    body = _call_inject("xai")
    assert "tools" not in body, "xAI must not receive a type:mcp tools entry"


def test_xai_mcp_skip_logs_info(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(
        logging.INFO, logger="services.universal_cloud_proxy.native_routes"
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

"""Unit tests for _inject_native_mcp xAI defensive guard.

Verifies that -mcp suffix requests for xAI are forwarded without injecting
a type:mcp tool entry (xAI does not yet support it), and that OpenAI still
receives the tool entry.
"""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.responses import JSONResponse
from starlette.requests import Request

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
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
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
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
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
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
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
    toolset = body["tools"][0]
    assert toolset["type"] == "mcp_toolset"
    assert toolset["mcp_server_name"] == "vortex"


def test_anthropic_inject_skipped_when_config_token_equals_callback_body(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
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


def _anthropic_toolset(monkeypatch: pytest.MonkeyPatch, tmp_path, **kwargs) -> dict:
    cfg = tmp_path / "mcp.yaml"
    cfg.write_text("auth_token: admin\nanthropic_callback_token: callback-tok\n")
    monkeypatch.setenv("MCP_CONFIG_PATH", str(cfg))
    body: dict = {"model": "claude-haiku-4-5-20251001"}
    with (
        patch(_PATCH_EXECUTOR, return_value=MagicMock()),
        patch(_PATCH_CONFIG, return_value={"url": _FAKE_CONFIG["url"], "token": ""}),
    ):
        inject_native_mcp("anthropic", body, **kwargs)
    return body["tools"][0]


def test_inject_native_mcp_allowlist_defaults_rag_fs_cortex(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Default anthropic-api inject disables the catalog and enables rag, fs, cortex."""
    toolset = _anthropic_toolset(monkeypatch, tmp_path)
    assert toolset["default_config"] == {"enabled": False}
    assert toolset["configs"] == {
        "cortex": {"enabled": True},
        "fs": {"enabled": True},
        "rag": {"enabled": True},
    }


def test_inject_native_mcp_explicit_tool_list_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A caller-supplied name outside the default appears in configs."""
    toolset = _anthropic_toolset(monkeypatch, tmp_path, enabled_tools=["team_dispatch"])
    assert "team_dispatch" in toolset["configs"]
    assert toolset["configs"]["team_dispatch"] == {"enabled": True}
    assert "rag" not in toolset["configs"]
    assert toolset["default_config"] == {"enabled": False}


def test_inject_native_mcp_cache_control_ephemeral_on_toolset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """cache_control ephemeral sits on the Anthropic toolset and not on OpenAI."""
    toolset = _anthropic_toolset(monkeypatch, tmp_path)
    assert toolset["cache_control"] == {"type": "ephemeral"}
    openai_body = _call_inject("openai")
    assert "cache_control" not in openai_body["tools"][0]


def test_inject_native_mcp_reverts_to_all_tools_no_cache_control(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Policy off restores no tool config and no cache_control (full catalog)."""
    monkeypatch.setattr(native_mcp_inject, "_APPLY_ANTHROPIC_TOOLSET_POLICY", False)
    toolset = _anthropic_toolset(monkeypatch, tmp_path)
    assert "default_config" not in toolset
    assert "configs" not in toolset
    assert "cache_control" not in toolset
    assert toolset == {"type": "mcp_toolset", "mcp_server_name": "vortex"}


def _anthropic_mcp_request(headers: list[tuple[bytes, bytes]]) -> Request:
    scope = {
        "type": "http",
        "asgi": {"spec_version": "2.3", "version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": "/api/v1/providers/anthropic/messages",
        "raw_path": b"/api/v1/providers/anthropic/messages",
        "root_path": "",
        "scheme": "http",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 80),
    }
    return Request(scope)


class _RecordingForwarder:
    def __init__(self) -> None:
        self.recorded: list[dict] = []

    def adapter_type(self, provider: str) -> str:
        _ = provider
        return "anthropic"

    async def forward_native(self, *, provider: str, request_body: dict) -> dict:
        _ = provider
        self.recorded.append(dict(request_body))
        return {"id": "msg_test", "content": [{"type": "text", "text": "ok"}]}


def _forward_anthropic_mcp(headers: list[tuple[bytes, bytes]]) -> dict:
    """Run forward_native for one Anthropic -mcp body; return the upstream body."""
    from services.universal_cloud_proxy.native_forwarders import forward_native

    request = _anthropic_mcp_request(headers)
    forwarder = _RecordingForwarder()
    body = {
        "model": "claude-haiku-4-5-20251001-mcp",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 16,
    }
    with (
        patch(_PATCH_EXECUTOR, return_value=MagicMock()),
        patch(_PATCH_CONFIG, return_value={"url": _FAKE_CONFIG["url"], "token": ""}),
        patch(
            "services.universal_cloud_proxy.cloud_proxy._read_json_object_body",
            new_callable=AsyncMock,
            return_value=body,
        ),
        patch(
            "services.universal_cloud_proxy.cloud_proxy._get_catalog",
            return_value=None,
        ),
    ):
        resp = asyncio.run(
            forward_native(
                request,
                provider_key="anthropic",
                surface="test",
                forwarder=forwarder,
                event_bus=None,
            )
        )
    assert isinstance(resp, JSONResponse)
    assert json.loads(resp.body)["id"] == "msg_test"
    assert forwarder.recorded
    return forwarder.recorded[0]


def test_forward_native_team_dispatch_mcp_keeps_agentic_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A pipeline -mcp request with no tool list still enables team_dispatch and agent_bus.

    Breaks when forward_native omits enabled_tools and the probe set is applied.
    """
    cfg = tmp_path / "mcp.yaml"
    cfg.write_text("auth_token: admin\nanthropic_callback_token: callback-tok\n")
    monkeypatch.setenv("MCP_CONFIG_PATH", str(cfg))
    upstream = _forward_anthropic_mcp([(b"x-pipeline-internal", b"true")])
    configs = upstream["tools"][-1]["configs"]
    assert "team_dispatch" in configs
    assert "agent_bus" in configs
    assert configs["team_dispatch"] == {"enabled": True}
    assert configs["agent_bus"] == {"enabled": True}


def test_forward_native_probe_mcp_is_rag_fs_cortex(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """An anthropic-api probe -mcp request enables only rag, fs, and cortex.

    Breaks when the agentic set is applied to a known read-only caller.
    """
    cfg = tmp_path / "mcp.yaml"
    cfg.write_text("auth_token: admin\nanthropic_callback_token: callback-tok\n")
    monkeypatch.setenv("MCP_CONFIG_PATH", str(cfg))
    upstream = _forward_anthropic_mcp([(b"x-ulg-client", b"anthropic-api")])
    configs = upstream["tools"][-1]["configs"]
    assert set(configs) == {"rag", "fs", "cortex"}
    assert "team_dispatch" not in configs
    assert "agent_bus" not in configs

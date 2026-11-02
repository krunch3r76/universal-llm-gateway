"""web_tab_write discovery: mount surface, production listing, uncached failure."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import yaml
from fastmcp import FastMCP
from request_profile import bind_request

from tools.web_tab_read import (
    RegistryUnavailableError,
    _ids_from_production_listing,
    clear_installed_pipeline_cache,
)
from tools.web_tab_write import register_web_tab_write_tool

_LISTING = {
    "description": "web tab",
    "members": {
        "web-tab-status": {
            "category": "web_tab",
            "url": "/api/v1/capabilities/web_tab/web-tab-status",
            "steps": 1,
            "version": "1.0",
        },
        "web-tab-write": {
            "category": "web_tab",
            "url": "/api/v1/capabilities/web_tab/web-tab-write",
            "steps": 1,
            "version": "1.0",
        },
    },
}


def _registered_tool():
    mcp = FastMCP("web-tab-write-test")
    register_web_tab_write_tool(mcp)
    tools = asyncio.run(mcp.list_tools())
    return next(tool for tool in tools if tool.name == "web_tab_write").fn


def test_write_op_relays_to_web_tab_write_pipeline() -> None:
    clear_installed_pipeline_cache()
    fn = _registered_tool()
    captured: dict = {}

    def _run(pipeline_id, messages, options, timeout):  # noqa: ANN001
        captured["pipeline_id"] = pipeline_id
        captured["options"] = options
        return {"content": "{}"}

    with (
        patch("tools.pipeline._pipeline_list", return_value=_LISTING),
        patch("tools.web_tab_write._pipeline_run", side_effect=_run),
        bind_request("default", surface="code", caller_identity="oauth:real"),
    ):
        result = fn(
            op="paste",
            cdp_url="http://127.0.0.1:9342",
            target_id="t1",
            text="hello",
            grant="explicit",
            authorization_source="agent-bus:21642#13",
        )
    assert "error" not in result
    assert captured["pipeline_id"] == "web-tab-write"
    assert captured["options"]["op"] == "paste"
    assert captured["options"]["mcp_surface"] == "code"
    assert captured["options"]["grant"] == "explicit"


def test_write_not_loaded_without_web_tab_write_pipeline() -> None:
    clear_installed_pipeline_cache()
    fn = _registered_tool()
    listing = {
        "members": {
            "web-tab-status": {
                "category": "web_tab",
                "url": "/api/v1/capabilities/web_tab/web-tab-status",
            }
        }
    }
    with patch("tools.pipeline._pipeline_list", return_value=listing):
        result = fn(
            op="open",
            cdp_url="http://127.0.0.1:9342",
            url="https://chatgpt.com/c/x",
        )
    assert result["error"]["code"] == "op_not_loaded"


def test_registry_unavailable_for_write() -> None:
    clear_installed_pipeline_cache()
    fn = _registered_tool()
    with patch(
        "tools.pipeline._pipeline_list",
        return_value={"error": {"code": "stargate_http_error"}},
    ):
        result = fn(
            op="bind",
            cdp_url="http://127.0.0.1:9342",
            target_id="t1",
            conversation_url="https://chatgpt.com/c/x",
        )
    assert result["error"]["code"] == "registry_unavailable"


def test_production_listing_includes_web_tab_write() -> None:
    assert _ids_from_production_listing(_LISTING) == frozenset(
        {"web-tab-status", "web-tab-write"}
    )


def test_canonical_write_schemas_cdp_url_optional() -> None:
    root = Path(__file__).resolve().parents[3]
    entries = yaml.safe_load((root / "config/mcp/canonical.yaml").read_text())
    write_names = {
        f"web_tab_write_{op}"
        for op in ("open", "bind", "paste", "submit", "verify_committed", "read_reply")
    }
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("canonical_name")
        if name not in write_names:
            continue
        required = entry.get("json_schema", {}).get("required") or []
        assert "cdp_url" not in required, f"{name} must not require cdp_url"
        if name in {"web_tab_write_open", "web_tab_write_bind", "web_tab_write_paste"}:
            assert "grant" in required
            assert "authorization_source" in required


def test_write_resolves_cdp_from_surface_when_omitted() -> None:
    clear_installed_pipeline_cache()
    fn = _registered_tool()
    captured: dict = {}

    def _run(pipeline_id, messages, options, timeout):  # noqa: ANN001
        captured["options"] = options
        return {"content": "{}"}

    with (
        patch("tools.pipeline._pipeline_list", return_value=_LISTING),
        patch("tools.web_tab_write._pipeline_run", side_effect=_run),
        bind_request("default", surface="code", caller_identity="oauth:real"),
    ):
        result = fn(
            op="open",
            surface="anthropic_console",
            url="https://console.anthropic.com/sign-in",
            grant="explicit",
            authorization_source="agent-bus:21642#13",
        )
    assert "error" not in result
    assert captured["options"]["surface"] == "anthropic_console"
    assert "cdp_url" not in captured["options"]


def test_list_shaped_members_are_unavailable() -> None:
    clear_installed_pipeline_cache()
    try:
        _ids_from_production_listing({"members": [{"name": "web-tab-write"}]})
    except RegistryUnavailableError as exc:
        assert "members_not_object" in str(exc)
    else:
        raise AssertionError("list members were accepted")

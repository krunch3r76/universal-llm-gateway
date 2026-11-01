"""web_tab_read discovery: mount surface, production listing, uncached failure."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from fastmcp import FastMCP
from request_profile import bind_request

from tools.web_tab_read import (
    RegistryUnavailableError,
    _fetch_installed_from_stargate,
    _ids_from_production_listing,
    clear_installed_pipeline_cache,
    register_web_tab_read_tool,
)

_LISTING = {
    "description": "web tab",
    "members": {
        "web-tab-status": {
            "category": "web_tab",
            "url": "/api/v1/capabilities/web_tab/web-tab-status",
            "steps": 1,
            "version": "1.0",
        },
        "web-tab-probe": {
            "category": "web_tab",
            "url": "/api/v1/capabilities/web_tab/web-tab-probe",
            "steps": 1,
            "version": "1.0",
        },
    },
}


def _registered_tool():
    mcp = FastMCP("web-tab-read-test")
    register_web_tab_read_tool(mcp)
    tools = asyncio.run(mcp.list_tools())
    return next(tool for tool in tools if tool.name == "web_tab_read").fn


def test_registered_tool_does_not_stamp_mount_surface() -> None:
    """Mount surface code is stamped as mcp_surface and is not the product surface."""
    clear_installed_pipeline_cache()
    fn = _registered_tool()
    captured: dict = {}

    def _run(pipeline_id, messages, options, timeout):  # noqa: ANN001
        captured["pipeline_id"] = pipeline_id
        captured["options"] = options
        return {"content": "{}"}

    with (
        patch("tools.pipeline._pipeline_list", return_value=_LISTING),
        patch("tools.web_tab_read._pipeline_run", side_effect=_run),
        bind_request("default", surface="code", caller_identity="oauth:real"),
    ):
        result = fn(op="status", house_id="21207", agent="orion")
    assert "error" not in result
    assert captured["pipeline_id"] == "web-tab-status"
    options = captured["options"]
    assert "surface" not in options
    assert options["mcp_surface"] == "code"
    assert options["house_id"] == "21207"


def test_explicit_product_surface_is_forwarded() -> None:
    """An explicit tool surface argument stays the product surface beside the mount."""
    clear_installed_pipeline_cache()
    fn = _registered_tool()
    captured: dict = {}

    def _run(pipeline_id, messages, options, timeout):  # noqa: ANN001
        captured["options"] = options
        return {"content": "{}"}

    with (
        patch("tools.pipeline._pipeline_list", return_value=_LISTING),
        patch("tools.web_tab_read._pipeline_run", side_effect=_run),
        bind_request("default", surface="code"),
    ):
        fn(op="probe", surface="chatgpt")
    assert captured["options"]["surface"] == "chatgpt"
    assert captured["options"]["mcp_surface"] == "code"


def test_registry_unavailable_not_claimed_installed() -> None:
    """A Stargate error is registry_unavailable and is not reported as an empty install."""
    clear_installed_pipeline_cache()
    fn = _registered_tool()
    with patch(
        "tools.pipeline._pipeline_list",
        return_value={"error": {"code": "stargate_http_error"}},
    ):
        result = fn(op="status", house_id="21207", agent="orion")
    assert result["error"]["code"] == "registry_unavailable"
    assert "advertised_ops" not in result["error"]


def test_list_shaped_members_are_unavailable() -> None:
    """A satellite-style members list is not an empty pipeline install."""
    clear_installed_pipeline_cache()
    try:
        _ids_from_production_listing({"members": [{"name": "web-tab-status"}]})
    except RegistryUnavailableError as exc:
        assert "members_not_object" in str(exc)
    else:
        raise AssertionError("list members were accepted")


def test_error_not_cached_and_recovery() -> None:
    """A failed listing is not cached, so the next successful listing is visible."""
    clear_installed_pipeline_cache()
    fn = _registered_tool()
    calls = {"n": 0}

    def _list(category=None):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] == 1:
            return {"error": {"code": "http_500"}}
        return _LISTING

    captured: dict = {}

    def _run(pipeline_id, messages, options, timeout):  # noqa: ANN001
        captured["pipeline_id"] = pipeline_id
        return {"content": "{}"}

    with (
        patch("tools.pipeline._pipeline_list", side_effect=_list),
        patch("tools.web_tab_read._pipeline_run", side_effect=_run),
    ):
        first = fn(op="status")
        second = fn(op="status")
    assert first["error"]["code"] == "registry_unavailable"
    assert second.get("content") == "{}"
    assert captured["pipeline_id"] == "web-tab-status"
    assert calls["n"] == 2


def test_import_error_is_registry_unavailable() -> None:
    """ImportError while loading the pipeline client is registry_unavailable, not installed."""
    def _boom():
        raise ImportError("tools.pipeline")

    with patch("tools.web_tab_read._load_pipeline_list", side_effect=_boom):
        try:
            _fetch_installed_from_stargate()
        except RegistryUnavailableError as exc:
            assert str(exc) == "pipeline_import"
        else:
            raise AssertionError("import failure claimed a listing")


def test_production_listing_selects_category() -> None:
    """Production member summaries contribute an id only when category is web_tab."""
    mixed = {
        "members": {
            "web-tab-status": {
                "category": "web_tab",
                "url": "/api/v1/capabilities/web_tab/web-tab-status",
            },
            "other": {
                "category": "demo",
                "url": "/api/v1/capabilities/demo/other",
            },
        }
    }
    assert _ids_from_production_listing(mixed) == frozenset({"web-tab-status"})

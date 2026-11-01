"""Pure installed-id mapping for web_tab_read. No Stargate client."""

from __future__ import annotations

import inspect

from web_tab import mcp_registry
from web_tab.mcp_registry import WEB_TAB_READ_OP_TO_PIPELINE, advertised_read_ops


def test_partial_install_maps_only_present_ids() -> None:
    """Only pipeline ids present in the supplied set become advertised read ops."""
    installed = frozenset({"web-tab-status"})
    assert advertised_read_ops(installed_pipeline_ids=installed) == frozenset(
        {"status"}
    )


def test_full_install_maps_both_ops() -> None:
    """Both status and probe ops map when both pipeline ids are installed."""
    installed = frozenset(WEB_TAB_READ_OP_TO_PIPELINE.values())
    assert advertised_read_ops(installed_pipeline_ids=installed) == frozenset(
        {"status", "probe"}
    )


def test_mcp_registry_has_no_tools_import() -> None:
    """Library mapping must not import the MCP service package or tools."""
    source = inspect.getsource(mcp_registry)
    assert "tools." not in source
    assert "import tools" not in source

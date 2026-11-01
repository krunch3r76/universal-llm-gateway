"""Installed pipeline discovery for web_tab_read."""

from __future__ import annotations

from unittest.mock import patch

from web_tab.mcp_registry import (
    WEB_TAB_READ_OP_TO_PIPELINE,
    advertised_read_ops,
    query_installed_web_tab_pipeline_ids,
)


def test_resolve_queries_registry() -> None:
    with patch("web_tab.mcp_registry._fetch_installed_from_stargate") as fetch:
        fetch.return_value = frozenset({"web-tab-status"})
        assert advertised_read_ops() == frozenset({"status"})


def test_missing_pipeline_partial_install() -> None:
    installed = frozenset({"web-tab-status"})
    assert advertised_read_ops(installed_pipeline_ids=installed) == frozenset({"status"})


def test_query_parses_stargate_members() -> None:
    resp = {
        "members": {
            "web-tab-status": {"category": "web_tab"},
            "other": {"category": "demo"},
        }
    }
    with patch("tools.pipeline._pipeline_list", return_value=resp):
        ids = query_installed_web_tab_pipeline_ids()
    assert ids == frozenset({"web-tab-status"})
    assert WEB_TAB_READ_OP_TO_PIPELINE["status"] in ids

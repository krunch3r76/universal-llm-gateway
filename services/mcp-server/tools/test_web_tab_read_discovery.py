"""AC3: advertised ops match installed web_tab read pipelines."""

from __future__ import annotations

from web_tab.mcp_registry import WEB_TAB_READ_OP_TO_PIPELINE, advertised_read_ops

from tools.web_tab_read import _resolve_pipeline


def test_advertised_ops_are_loaded() -> None:
    installed = frozenset(WEB_TAB_READ_OP_TO_PIPELINE.values())
    assert advertised_read_ops(installed_pipeline_ids=installed) == frozenset(
        {"status", "probe"}
    )
    assert _resolve_pipeline("status", installed=installed) == "web-tab-status"
    assert _resolve_pipeline("probe", installed=installed) == "web-tab-probe"


def test_missing_pipeline_partial_install() -> None:
    installed = frozenset({"web-tab-status"})
    assert advertised_read_ops(installed_pipeline_ids=installed) == frozenset({"status"})
    assert _resolve_pipeline("probe", installed=installed) is None

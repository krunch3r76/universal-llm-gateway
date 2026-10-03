"""The MCP fold helper matches the Stargate copy."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_MCP = _REPO / "services" / "mcp-server"
_STARGATE = _REPO / "services" / "universal-stargate"
for path in (str(_MCP), str(_STARGATE)):
    if path not in sys.path:
        sys.path.insert(0, path)

from systems.pipeline.core import step_controls as stargate_controls  # noqa: E402

from tools import _rag_relay_options as mcp_controls  # noqa: E402

_CASES = [
    {},
    {"hyde_enabled": True, "rerank_enabled": False, "scope_override": "claude_api"},
    {"catalog_retry_enabled": True, "rag_max_chunks": 3},
    {"step_overrides": {"rerank": {"enabled": True}}, "skip_steps": ["generate_hyde"]},
    {"step_overrides": {}, "hyde_enabled": False},
]


def test_legacy_flag_tables_match() -> None:
    assert mcp_controls.LEGACY_ENABLE_FLAGS == stargate_controls.LEGACY_ENABLE_FLAGS


def test_fold_and_finalize_match_on_both_targets() -> None:
    for options in _CASES:
        assert mcp_controls.fold_legacy_enable_flags(
            options
        ) == stargate_controls.fold_legacy_enable_flags(options)
        for target in ("rag-search", "rag-context", "other"):
            assert mcp_controls.finalize_relay_pipeline_options(
                target, options
            ) == stargate_controls.finalize_relay_pipeline_options(target, options)

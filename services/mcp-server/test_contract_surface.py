"""Hermetic tests for contract-scoped MCP primary tool derivation (G5)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MCP_SERVER_DIR = REPO_ROOT / "services" / "mcp-server"
CANONICAL_YAML = REPO_ROOT / "config" / "mcp" / "canonical.yaml"

sys.path.insert(0, str(MCP_SERVER_DIR))
os.environ.setdefault("MCP_AUTH_TOKEN", "test-noop")
os.environ.setdefault("MCP_OAUTH_DISABLED", "1")

from endpoint_surface import (  # noqa: E402
    derive_contract_primary_tools,
    derive_surface_primary_tools,
)

IMPLEMENT_ALLOW = frozenset(
    {
        "cortex",
        "cortex_brief",
        "agent_bus",
        "agent_bus_read",
        "fs",
        "tool_search",
        "dispatch",
        "manage",
        "observability",
    }
)
LIFE_ONLY = frozenset({"imprint", "delegate", "notify"})
HIDDEN_FROM_IMPLEMENT = frozenset(
    {
        "rag",
        "retrieve",
        "cursor_request",
        "pipeline",
        "team_dispatch",
        "panel_dispatch",
        "project_ask",
    }
)


def test_contract_primary_domains_implement_matches_spec() -> None:
    tools = derive_contract_primary_tools("implement", CANONICAL_YAML)
    assert tools == IMPLEMENT_ALLOW


def test_pure_mechanical_shares_implement_allow_list() -> None:
    assert derive_contract_primary_tools(
        "pure-mechanical", CANONICAL_YAML
    ) == derive_contract_primary_tools("implement", CANONICAL_YAML)


def test_light_bounded_falls_back_to_code_primaries() -> None:
    code = derive_surface_primary_tools("code", CANONICAL_YAML)
    assert derive_contract_primary_tools("light-bounded", CANONICAL_YAML) == code
    assert derive_contract_primary_tools(None, CANONICAL_YAML) == code
    assert derive_contract_primary_tools("", CANONICAL_YAML) == code


def test_implement_hides_team_dispatch() -> None:
    tools = derive_contract_primary_tools("implement", CANONICAL_YAML)
    assert "team_dispatch" not in tools


def test_allow_list_subset_of_code_primaries() -> None:
    code = derive_surface_primary_tools("code", CANONICAL_YAML)
    implement = derive_contract_primary_tools("implement", CANONICAL_YAML)
    assert implement <= code


def test_allow_list_disjoint_from_life_only_tools() -> None:
    implement = derive_contract_primary_tools("implement", CANONICAL_YAML)
    assert not (implement & LIFE_ONLY)


def test_hidden_tools_are_code_only_extras() -> None:
    code = derive_surface_primary_tools("code", CANONICAL_YAML)
    implement = derive_contract_primary_tools("implement", CANONICAL_YAML)
    assert HIDDEN_FROM_IMPLEMENT <= code
    assert HIDDEN_FROM_IMPLEMENT.isdisjoint(implement)

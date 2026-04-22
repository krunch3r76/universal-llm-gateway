"""Sanity tests for agent_seat tool definitions.

Tool schemas are pure data; the tests verify shape + required fields so any
accidental edit (typo in a key, wrong tier, missing required arg) is caught
at pytest time rather than at runtime inside a dispatched agent.
"""

from __future__ import annotations

import pytest

from agent_seat.tools import (
    CORTEX_TOOL_DEFINITION,
    RAG_SEARCH_TOOL_DEFINITION,
    TEAM_TOOL_DEFINITIONS,
    TOOL_DEFINITIONS,
    TOOL_REGISTRY,
    resolve_tools,
)


def _tool_names(defs: list[dict]) -> list[str]:
    return [d["function"]["name"] for d in defs]


def test_read_tier_has_expected_tools() -> None:
    names = _tool_names(TOOL_DEFINITIONS)
    assert set(names) == {"cortex", "rag_search"}


def test_team_tier_has_expected_tools() -> None:
    names = _tool_names(TEAM_TOOL_DEFINITIONS)
    assert set(names) == {"cortex", "agent_bus"}


def test_all_tools_have_openai_function_shape() -> None:
    for defn in TOOL_DEFINITIONS + TEAM_TOOL_DEFINITIONS:
        assert defn["type"] == "function"
        fn = defn["function"]
        assert "name" in fn
        assert "description" in fn and fn["description"]
        assert "parameters" in fn
        assert fn["parameters"]["type"] == "object"
        assert "properties" in fn["parameters"]


def test_required_fields_are_subset_of_properties() -> None:
    for defn in TOOL_DEFINITIONS + TEAM_TOOL_DEFINITIONS:
        params = defn["function"]["parameters"]
        required = params.get("required", [])
        props = params["properties"].keys()
        for r in required:
            assert r in props, (
                f"{defn['function']['name']}: required {r!r} not in properties"
            )


def test_rag_search_constant_matches_read_tier_entry() -> None:
    read_rag = next(
        d for d in TOOL_DEFINITIONS if d["function"]["name"] == "rag_search"
    )
    assert read_rag is RAG_SEARCH_TOOL_DEFINITION


def test_cortex_dispatch_requires_tool_field() -> None:
    for defs in (TOOL_DEFINITIONS, TEAM_TOOL_DEFINITIONS):
        cortex = next(d for d in defs if d["function"]["name"] == "cortex")
        required = cortex["function"]["parameters"]["required"]
        assert "tool" in required
        assert cortex is CORTEX_TOOL_DEFINITION


def test_tool_registry_resolve_known() -> None:
    defs, execs = resolve_tools(["cortex", "agent_bus"])
    assert len(defs) == 2
    assert execs == ["cortex_dispatch", "agent_bus_dispatch"]
    assert defs[0] == TOOL_REGISTRY["cortex"]["definition"]


def test_tool_registry_resolve_unknown_raises() -> None:
    with pytest.raises(ValueError, match="unknown tool"):
        resolve_tools(["cortex", "nonsense"])


def test_legacy_tier_constants_match_registry() -> None:
    assert TOOL_DEFINITIONS == [
        TOOL_REGISTRY["cortex"]["definition"],
        TOOL_REGISTRY["rag_search"]["definition"],
    ]
    assert TEAM_TOOL_DEFINITIONS == [
        TOOL_REGISTRY["cortex"]["definition"],
        TOOL_REGISTRY["agent_bus"]["definition"],
    ]

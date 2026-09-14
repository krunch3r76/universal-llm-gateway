"""Regression tests for graph_utils.extract_entity_ids (friction 23548)."""

from __future__ import annotations

from .graph_utils import extract_entity_ids


def test_extract_entity_ids_maps_agent_bus_numeric_to_thread() -> None:
    """R19 / a:33394: house pointers resolve to thread:{id}, not an agent-bus type."""
    assert extract_entity_ids("see agent-bus:1234") == {"thread:1234"}


def test_extract_entity_ids_maps_agent_bus_alongside_other_refs() -> None:
    assert extract_entity_ids("agent-bus:10479 and todo:r19-bus-thread-entities") == {
        "thread:10479",
        "todo:r19-bus-thread-entities",
    }


def test_extract_entity_ids_keeps_legitimate_entity_refs() -> None:
    assert extract_entity_ids("see agent_skill:foo bar") == {"agent_skill:foo"}

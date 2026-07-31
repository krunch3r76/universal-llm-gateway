"""Regression tests for graph_utils.extract_entity_ids (friction 23548)."""

from __future__ import annotations

from .graph_utils import extract_entity_ids


def test_extract_entity_ids_ignores_agent_bus_thread_pointer() -> None:
    assert extract_entity_ids("see agent-bus:1234") == set()


def test_extract_entity_ids_keeps_legitimate_entity_refs() -> None:
    assert extract_entity_ids("see agent_skill:foo bar") == {"agent_skill:foo"}

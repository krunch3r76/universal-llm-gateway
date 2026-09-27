"""Tests for IDE catalog elision before tape budget measurement."""

from __future__ import annotations

import pytest
from agent_bus_store.tape_catalog_elide import elide_ide_catalog_blocks
from agent_bus_store.tape_degrade import payload_bytes

pytestmark = pytest.mark.offline


def test_elides_cursor_commands_outside_user_query() -> None:
    harness = "x" * 200
    content = (
        f"<cursor_commands>{harness}</cursor_commands>\n"
        "<user_query>implement the fix</user_query>"
    )
    messages = [{"role": "user", "content": content, "turn_index": 1}]
    out = elide_ide_catalog_blocks(messages)
    assert "[elided cursor_commands:" in out[0]["content"]
    assert "implement the fix" in out[0]["content"]
    assert harness not in out[0]["content"]


def test_keeps_cursor_commands_when_only_operator_text() -> None:
    content = "<cursor_commands>/checkpoint</cursor_commands>"
    messages = [{"role": "user", "content": content, "turn_index": 1}]
    out = elide_ide_catalog_blocks(messages)
    assert out[0]["content"] == content


def test_elision_runs_before_budget_decision() -> None:
    big = "y" * 4000
    content = (
        f"<manually_attached_skills>{big}</manually_attached_skills>\n"
        "<user_query>hi</user_query>"
    )
    messages = [{"role": "user", "content": content, "turn_index": 1}]
    raw_budget = payload_bytes(messages, [])
    elided = elide_ide_catalog_blocks(messages)
    elided_budget = payload_bytes(elided, [])
    assert elided_budget < raw_budget
    assert "[elided manually_attached_skills:" in elided[0]["content"]


def test_non_user_messages_unchanged() -> None:
    messages = [{"role": "assistant", "content": "<cursor_commands>x</cursor_commands>"}]
    assert elide_ide_catalog_blocks(messages) == messages

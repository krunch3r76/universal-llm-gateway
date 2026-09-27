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
    messages = [
        {"role": "assistant", "content": "<cursor_commands>x</cursor_commands>"}
    ]
    assert elide_ide_catalog_blocks(messages) == messages


def test_block_inside_user_query_kept_after_earlier_elision() -> None:
    """A preceding elision must not shift the user_query guard off its span."""
    harness = "x" * 500
    content = (
        f"<cursor_commands>{harness}</cursor_commands>\n"
        "<user_query>keep <dynamic_tool_catalog>inner</dynamic_tool_catalog> tail"
        "</user_query>"
    )
    out = elide_ide_catalog_blocks([{"role": "user", "content": content}])
    assert harness not in out[0]["content"]
    assert "<dynamic_tool_catalog>inner</dynamic_tool_catalog>" in out[0]["content"]


def test_second_block_after_elision_still_elided() -> None:
    content = (
        "<cursor_commands>a</cursor_commands>"
        "<dynamic_tool_catalog>b</dynamic_tool_catalog>"
        "<user_query>q</user_query>"
    )
    out = elide_ide_catalog_blocks([{"role": "user", "content": content}])
    assert "[elided cursor_commands:" in out[0]["content"]
    assert "[elided dynamic_tool_catalog:" in out[0]["content"]
    assert "<user_query>q</user_query>" in out[0]["content"]


def test_every_user_query_span_is_protected() -> None:
    content = (
        "<user_query></user_query>\n"
        "<manually_attached_skills>SKILL</manually_attached_skills>\n"
        "<user_query>second <open_and_recently_viewed_files>f"
        "</open_and_recently_viewed_files></user_query>"
    )
    out = elide_ide_catalog_blocks([{"role": "user", "content": content}])
    assert "SKILL" not in out[0]["content"]
    assert (
        "<open_and_recently_viewed_files>f</open_and_recently_viewed_files>"
        in (out[0]["content"])
    )


def test_unknown_tag_passes_through() -> None:
    content = "<foo_catalog>zzz</foo_catalog><user_query>q</user_query>"
    assert (
        elide_ide_catalog_blocks([{"role": "user", "content": content}])[0]["content"]
        == content
    )

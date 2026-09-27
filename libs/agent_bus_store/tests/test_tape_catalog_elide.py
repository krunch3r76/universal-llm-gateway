"""Tests for IDE catalog elision before tape budget measurement."""

from __future__ import annotations

import pytest
from agent_bus_store.tape_catalog_elide import (
    elide_ide_catalog_blocks,
    exclude_ide_catalog_turns,
)
from agent_bus_store.tape_degrade import payload_bytes
from agent_bus_store.tape_verbal import project_role_content_list

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


def _catalog_block(tag: str, total_bytes: int) -> str:
    open_tag = f"<{tag}>"
    close_tag = f"</{tag}>"
    inner = total_bytes - len(open_tag.encode()) - len(close_tag.encode())
    return open_tag + ("x" * inner) + close_tag


def _catalog_turn_95d4aea6_shaped() -> str:
    """Sibling blocks sized like transcript 95d4aea6's catalog user message.

    Observed shape: ``available_subagent_types`` 3258 B, a blank line,
    ``available_subagent_models`` 1175 B, a blank line, ``dynamic_tools``
    9142 B. No ``<user_query>``. UTF-8 length 13579.
    """
    text = "\n\n".join(
        (
            _catalog_block("available_subagent_types", 3258),
            _catalog_block("available_subagent_models", 1175),
            _catalog_block("dynamic_tools", 9142),
        )
    )
    assert len(text.encode("utf-8")) == 13579
    return text


def test_95d4aea6_shaped_catalog_turn_excluded_from_verbal_tape() -> None:
    catalog = _catalog_turn_95d4aea6_shaped()
    assert "<user_query>" not in catalog
    messages = [
        {"role": "user", "content": catalog, "turn_index": 1},
        {
            "role": "user",
            "content": "<user_query>resume the house</user_query>",
            "turn_index": 2,
        },
        {"role": "assistant", "content": "picked up", "turn_index": 2},
    ]
    kept = exclude_ide_catalog_turns(messages)
    verbal = project_role_content_list(kept)
    assert catalog not in [row["content"] for row in verbal]
    assert all("available_subagent_types" not in row["content"] for row in verbal)
    assert [row["content"] for row in verbal] == [
        "<user_query>resume the house</user_query>",
        "picked up",
    ]
    assert payload_bytes(kept, []) < payload_bytes(messages, [])


def test_catalog_turn_with_substantive_user_query_kept() -> None:
    content = (
        _catalog_turn_95d4aea6_shaped() + "\n<user_query>resume</user_query>"
    )
    messages = [{"role": "user", "content": content}]
    assert exclude_ide_catalog_turns(messages) == messages


def test_catalog_blocks_plus_operator_prose_kept() -> None:
    content = _catalog_turn_95d4aea6_shaped() + "\n\nplease resume"
    messages = [{"role": "user", "content": content}]
    assert exclude_ide_catalog_turns(messages) == messages


def test_cursor_commands_only_is_not_a_catalog_turn() -> None:
    messages = [{"role": "user", "content": "<cursor_commands>/checkpoint</cursor_commands>"}]
    assert exclude_ide_catalog_turns(messages) == messages

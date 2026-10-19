"""Offline tests for block-shaped transcript rows projected to markdown.

These lock the indexer-facing contract: full fenced tool bodies, one-line
hidden-tag normalization, and a source-truncated mark when the input was
already cut. They do not touch the RAG service or the tape extractors.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from message_projection.project import project_messages

pytestmark = pytest.mark.offline

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_fixture() -> tuple[list[dict], dict]:
    payload = json.loads((FIXTURES / "sample_messages.json").read_text())
    return payload["messages"], payload["meta"]


def test_golden_fixture_matches_expected_markdown() -> None:
    """The anonymized sample projects byte-for-byte onto the checked-in markdown file."""
    messages, meta = _load_fixture()
    expected = (FIXTURES / "sample_expected.md").read_text().rstrip("\n")
    assert project_messages(messages, meta) == expected


def test_heading_includes_pt_and_role() -> None:
    """Each turn heading carries the PT timestamp and the message role."""
    messages, meta = _load_fixture()
    md = project_messages(messages, meta)
    assert "## Turn 3 - Sunday, Oct 18, 2026, 8:40 PM PT - user" in md
    assert md.count(" PT - ") >= 1


def test_tool_bodies_full_without_tool_call_substitute() -> None:
    """SendToUser, SendToAgent, Task, and Shell inputs stay complete and fenced."""
    messages, meta = _load_fixture()
    md = project_messages(messages, meta)
    assert "Full user-visible reply without summarization." in md
    assert "Full agent-visible body without summarization." in md
    assert (
        "Full task prompt body for the subagent must appear verbatim in projection."
        in md
    )
    assert "echo full-shell-command-literal" in md
    assert '"stdout": "ok"' in md
    full_turns = md.split("## Turn 9")[0]
    assert "[tool call:" not in full_turns


def test_background_task_ellipsis_preserved_and_marked_truncated() -> None:
    """A background-task label that already ends in an ellipsis is marked cut."""
    messages, meta = _load_fixture()
    md = project_messages(messages, meta)
    turn4 = md.split("## Turn 4")[1].split("## Turn 5")[0]
    assert 'Background task "partial label…"' in turn4
    assert turn4.rstrip().endswith("[source-truncated]")


def test_midstring_four_k_splice_preserved_and_marked() -> None:
    """A mid-string four-kilobyte ellipsis splice is preserved and marked cut."""
    messages, meta = _load_fixture()
    half = 2000
    splice = "a" * half + "..." + "b" * (4000 - half - 3)
    md = project_messages(messages, meta)
    assert splice in md
    turn5 = md.split("## Turn 5")[1].split("## Turn 6")[0]
    assert turn5.rstrip().endswith("[source-truncated]")


def test_longer_fence_when_payload_contains_three_backticks() -> None:
    """A payload that contains a three-backtick fence gets a longer fence."""
    messages, meta = _load_fixture()
    md = project_messages(messages, meta)
    assert "tool_use: FormatCheck\n````" in md
    assert "line with ``` three backticks inside" in md


def test_hidden_tags_normalized_in_body() -> None:
    """Hidden tags drop their bodies and a multi-line hidden prompt is one line."""
    messages, meta = _load_fixture()
    md = project_messages(messages, meta)
    assert "<timestamp>" not in md
    assert "<user_query>" not in md
    assert "<system_reminder>" not in md
    assert "<memory_context>" not in md
    assert "GROK_BOT_HIDDEN_PROMPT" not in md
    assert "must-not-leak-reminder" not in md
    assert "must-not-leak-memory" not in md
    assert "[system-reminder]" in md
    assert "[memory-context]" in md
    assert "Do the structural sample task." in md
    assert "only hidden" in md
    turn10 = md.split("## Turn 10")[1]
    body = turn10.split("\n\n", 1)[1]
    assert "\n" not in body
    assert body == "only hidden second hidden line must stay on the same line"


def test_project_messages_is_idempotent() -> None:
    """Repeating the call, including after a JSON round-trip, returns the same text."""
    messages, meta = _load_fixture()
    once = project_messages(messages, meta)
    twice = project_messages(messages, meta)
    replay = project_messages(json.loads(json.dumps(messages)), dict(meta))
    assert once == twice == replay


def test_tool_call_placeholder_without_input_is_source_truncated() -> None:
    """A source tool-call placeholder with no input is marked truncated, not fenced."""
    messages, meta = _load_fixture()
    md = project_messages(messages, meta)
    turn9 = md.split("## Turn 9")[1].split("## Turn 10")[0]
    assert "[tool call: SendToAgent]" in turn9
    assert "```" not in turn9
    assert turn9.rstrip().endswith("[source-truncated]")
    direct = project_messages(
        [{"role": "assistant", "content": "[tool call: Shell]"}],
        {"observed_at": "2026-10-18T20:40:00-07:00"},
    )
    assert "[tool call: Shell]" in direct
    assert direct.rstrip().endswith("[source-truncated]")


def test_tool_result_block_list_renders_full_text() -> None:
    """Tool-result text blocks are copied in full, including their newlines."""
    messages, meta = _load_fixture()
    md = project_messages(messages, meta)
    turn8 = md.split("## Turn 8")[1].split("## Turn 9")[0]
    assert "alpha line of the tool result\nbeta line stays intact" in turn8
    assert "second block also full BLOCKLISTEND" in turn8
    assert '"type": "text"' not in turn8
    assert "[source-truncated]" not in turn8


def test_eight_k_tool_result_present_in_full() -> None:
    """An eight-kilobyte tool result is copied whole, with every marker intact."""
    messages, meta = _load_fixture()
    md = project_messages(messages, meta)
    assert md.count("MARKER8K") == 1000


def test_empty_messages_returns_empty_string() -> None:
    """An empty message list projects to an empty string and adds no heading."""
    assert project_messages([], {"observed_at": "t"}) == ""

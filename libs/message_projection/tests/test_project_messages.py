"""Offline tests for block-shaped transcript -> markdown projection."""

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
    messages, meta = _load_fixture()
    expected = (FIXTURES / "sample_expected.md").read_text().rstrip("\n")
    assert project_messages(messages, meta) == expected


def test_heading_includes_pt_and_role() -> None:
    messages, meta = _load_fixture()
    md = project_messages(messages, meta)
    assert "## Turn 3 - Sunday, Oct 18, 2026, 8:40 PM PT - user" in md
    assert md.count(" PT - ") >= 1


def test_tool_bodies_full_without_tool_call_substitute() -> None:
    messages, meta = _load_fixture()
    md = project_messages(messages, meta)
    assert "Full user-visible reply without summarization." in md
    assert (
        "Full task prompt body for the subagent must appear verbatim in projection."
        in md
    )
    assert "echo full-shell-command-literal" in md
    assert '"stdout": "ok"' in md or '"stdout": "ok"' in md.replace("'", '"')
    assert "[tool call:" not in md


def test_background_task_ellipsis_preserved_and_marked_truncated() -> None:
    messages, meta = _load_fixture()
    md = project_messages(messages, meta)
    turn4 = md.split("## Turn 4")[1].split("## Turn 5")[0]
    assert 'Background task "partial label…"' in turn4
    assert turn4.rstrip().endswith("[source-truncated]")


def test_midstring_four_k_splice_preserved_and_marked() -> None:
    messages, meta = _load_fixture()
    half = 2000
    splice = "a" * half + "..." + "b" * (4000 - half - 3)
    md = project_messages(messages, meta)
    assert splice in md
    turn5 = md.split("## Turn 5")[1].split("## Turn 6")[0]
    assert turn5.rstrip().endswith("[source-truncated]")


def test_longer_fence_when_payload_contains_three_backticks() -> None:
    messages, meta = _load_fixture()
    md = project_messages(messages, meta)
    assert "tool_use: FormatCheck\n````" in md
    assert "line with ``` three backticks inside" in md


def test_hidden_tags_normalized_in_body() -> None:
    messages, meta = _load_fixture()
    md = project_messages(messages, meta)
    assert "<timestamp>" not in md
    assert "GROK_BOT_HIDDEN_PROMPT" not in md
    assert "[system-reminder]" in md
    assert "[memory-context]" in md
    assert "Do the structural sample task." in md
    assert "only hidden" in md


def test_project_messages_is_idempotent() -> None:
    messages, meta = _load_fixture()
    once = project_messages(messages, meta)
    twice = project_messages(messages, meta)
    assert once == twice


def test_eight_k_tool_result_present_in_full() -> None:
    messages, meta = _load_fixture()
    md = project_messages(messages, meta)
    assert md.count("MARKER8K") == 1000


def test_empty_messages_returns_empty_string() -> None:
    assert project_messages([], {"observed_at": "t"}) == ""

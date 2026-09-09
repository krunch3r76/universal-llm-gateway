"""Tests for seal_reader md-v1 parse hygiene (Phase 2 AC 2.1–2.3)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from continuity_tape.render_md import _EMPTY_ASSISTANT, _EMPTY_USER
from continuity_tape.seal_reader import parse_verbatim_md

pytestmark = pytest.mark.offline


def _verbatim_md(*turns: tuple[str, str | None, str | None]) -> str:
    lines = ["# Session test-sid", ""]
    for idx, (topic, user, assistant) in enumerate(turns, start=1):
        lines.extend(
            [
                f"## Turn {idx} — {topic}",
                "",
                "### User",
                "",
                user if user is not None else _EMPTY_USER,
                "",
                "### Assistant",
                "",
                (assistant if assistant is not None else _EMPTY_ASSISTANT),
                "",
            ]
        )
    return "\n".join(lines)


@patch("cortex_store.events_tape.transcript_legacy_md_read")
def test_parse_verbatim_md_maps_sentinels_to_null(mock_event) -> None:
    md = _verbatim_md(("t1", None, "hello"), ("t2", "hi", None))
    messages = parse_verbatim_md(
        md,
        seg={"transcript_id": "uuid-1", "turn_lo": 0, "turn_hi": 2},
        session_id="cursor-2026-09-09-120000-a01",
    )
    assert len(messages) == 4
    assert messages[0]["content"] is None
    assert messages[1]["content"] == "hello"
    assert messages[2]["content"] == "hi"
    assert messages[3]["content"] is None
    assert all(m.get("source") == "cursor-seal-md" for m in messages)
    mock_event.assert_called_once()
    assert mock_event.call_args.kwargs["sentinel_count"] == 2


@patch("cortex_store.events_tape.transcript_legacy_md_read")
def test_parse_verbatim_md_respects_turn_window(mock_event) -> None:
    md = _verbatim_md(
        ("one", "u1", "a1"),
        ("two", "u2", "a2"),
        ("three", "u3", "a3"),
    )
    messages = parse_verbatim_md(
        md,
        seg={"transcript_id": "uuid-1", "turn_lo": 1, "turn_hi": 2},
        session_id="sid",
    )
    assert len(messages) == 2
    assert messages[0]["turn_index"] == 2
    assert messages[0]["content"] == "u2"
    mock_event.assert_called_once()


@patch("cortex_store.events_tape.transcript_legacy_md_read")
def test_parse_verbatim_md_counts_user_marker_hits(mock_event) -> None:
    md = _verbatim_md(
        (
            "tools",
            "User said hi\n\n[tool call: grep]",
            "ok",
        ),
    )
    parse_verbatim_md(
        md,
        seg={"transcript_id": "uuid-1", "turn_lo": 0, "turn_hi": 1},
        session_id="sid",
    )
    assert mock_event.call_args.kwargs["user_marker_hits"] == 1


@patch("cortex_store.events_tape.transcript_legacy_md_read")
def test_parse_verbatim_md_counts_assistant_markers(mock_event) -> None:
    md = _verbatim_md(
        (
            "tools",
            "question",
            "[tool call: Read]\n\nAnswer.",
        ),
    )
    parse_verbatim_md(
        md,
        seg={"transcript_id": "uuid-1", "turn_lo": 0, "turn_hi": 1},
        session_id="sid",
    )
    assert mock_event.call_args.kwargs["marker_count"] == 1

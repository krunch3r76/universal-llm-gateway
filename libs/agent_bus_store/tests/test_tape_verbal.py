"""Verbal tape adapter — extras stripped, role/content kept, index role policy."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from agent_bus_store.tape_harvest import render_tape_with_harvest
from agent_bus_store.tape_render import (
    _filter_messages_to_cells,
    _last_session_cells,
    render_tape,
)
from agent_bus_store.tape_verbal import (
    VERBAL_KEYS,
    to_verbal_message,
    to_verbal_messages,
)

pytestmark = pytest.mark.offline

_MECHANICAL = {
    "role": "user",
    "content": "Resume from the last window.",
    "transcript_id": "c3d4e5f6-a7b8-9012-cdef-123456789012",
    "session_id": "cursor-2026-09-07-100000-sp1",
    "turn_index": 12,
    "window_whole": False,
    "bus_turn_id": 48,
    "transcript_span": "transcript:cursor-2026-09-07-100000-sp1#turn-12",
}

_INDEX = {
    "role": "index",
    "content": "transcript:cursor-2026-09-07-100000-sp1#turn-3",
    "transcript_span": "transcript:cursor-2026-09-07-100000-sp1#turn-3",
    "bus_turn_id": 5,
    "session_id": "cursor-2026-09-07-100000-sp1",
    "transcript_id": "c3d4e5f6-a7b8-9012-cdef-123456789012",
    "turn_index": 3,
}

_EXTRAS = (
    "transcript_id",
    "session_id",
    "turn_index",
    "window_whole",
    "bus_turn_id",
    "transcript_span",
)


def test_verbal_adapter_strips_transcript_id_turns_at_cp_equivalents_and_bus_turn_id() -> (
    None
):
    """Mechanical extras including turns@cp (turn_index) must not leak into verbal."""
    verbal = to_verbal_message(_MECHANICAL)
    for key in _EXTRAS:
        assert key not in verbal
    assert set(verbal) == set(VERBAL_KEYS)


def test_verbal_adapter_preserves_role_and_content() -> None:
    verbal = to_verbal_message(_MECHANICAL)
    assert verbal["role"] == "user"
    assert verbal["content"] == "Resume from the last window."
    assert to_verbal_message(_MECHANICAL) is not _MECHANICAL
    assert "transcript_id" in _MECHANICAL


def test_index_role_overflow_rows_stay_on_verbal_tape_with_role_content_only() -> None:
    """Index-role policy: overflow index rows are kept, not dropped or remapped."""
    verbal = to_verbal_message(_INDEX)
    assert verbal["role"] == "index"
    assert verbal["content"] == "transcript:cursor-2026-09-07-100000-sp1#turn-3"
    assert set(verbal) == {"role", "content"}
    for key in _EXTRAS:
        assert key not in verbal
    mapped = to_verbal_messages([_MECHANICAL, _INDEX])
    assert [m["role"] for m in mapped] == ["user", "index"]


def test_last_session_scope_keeps_only_last_cp_interval() -> None:
    cells = [
        {"cp_ordinal": 1, "transcript_id": "a", "turn_lo": 0, "turn_hi": 5, "bus_turn_id": 10},
        {"cp_ordinal": 2, "transcript_id": "a", "turn_lo": 5, "turn_hi": 12, "bus_turn_id": 20},
        {"cp_ordinal": 3, "transcript_id": "a", "turn_lo": 12, "turn_hi": 18, "bus_turn_id": None},
    ]
    scoped = _last_session_cells(cells)
    assert [c["cp_ordinal"] for c in scoped] == [2, 3]
    messages = [
        {"transcript_id": "a", "turn_index": 4, "role": "user", "content": "old"},
        {"transcript_id": "a", "turn_index": 8, "role": "user", "content": "last"},
        {"transcript_id": "a", "turn_index": 15, "role": "user", "content": "open"},
    ]
    filtered = _filter_messages_to_cells(messages, scoped)
    assert [m["content"] for m in filtered] == ["last", "open"]


def test_render_tape_format_verbal_adds_verbal_messages_keeps_mechanical() -> None:
    with (
        patch("agent_bus_store.tape_render.list_checkpoint_turns", return_value=()),
        patch("cortex_store.db.cortex_conn") as mock_conn,
        patch("cortex_store.events_tape.agent_bus_tape_rendered"),
    ):
        conn = mock_conn.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = []
        omitted = render_tape(thread_id="100")
        verbal = render_tape(thread_id="100", format="verbal")
    assert "verbal_messages" not in omitted
    assert omitted["messages"] == []
    assert verbal["messages"] == []
    assert verbal["verbal_messages"] == []


@patch("agent_bus_store.tape_harvest.render_tape")
def test_harvest_forwards_format_verbal_to_render_tape(mock_render) -> None:
    mock_render.return_value = {"messages": [], "verbal_messages": []}
    render_tape_with_harvest(
        thread_id="1",
        budget_bytes=1000,
        harvest=False,
        format="verbal",
    )
    assert mock_render.call_args.kwargs["format"] == "verbal"
    assert mock_render.call_args.kwargs["scope"] == "last_session"


@patch("agent_bus_store.resume_envelope._last_checkpoint_highlight", return_value="portable highlight")
@patch("agent_bus_store.resume_envelope.render_tape_with_harvest")
def test_resume_envelope_seal_pending_strips_unsealed_open_tail(
    mock_render,
    _mock_highlight,
) -> None:
    from agent_bus_store.resume_envelope import build_resume_envelope

    mock_render.return_value = {
        "messages": [
            {
                "role": "user",
                "content": "sealed cell",
                "transcript_id": "uuid-a",
                "turn_index": 5,
            },
            {
                "role": "user",
                "content": "open tail",
                "transcript_id": "uuid-a",
                "turn_index": 12,
            },
        ],
        "verbal_messages": [
            {"role": "user", "content": "sealed cell"},
            {"role": "user", "content": "open tail"},
        ],
        "open_line": {
            "scope": "last_session",
            "mismatch": [
                {
                    "transcript_id": "uuid-a",
                    "turns_at_cp": 12,
                    "sealed_turn_hi": 5,
                    "post_lid_turns": 0,
                }
            ],
            "last_cp": {
                "transcript_id": "uuid-a",
                "turn_hi": 12,
            },
        },
    }
    env = build_resume_envelope("10223")
    assert env["seal_status"] == "seal_pending"
    assert env["tape_verbal"] == [{"role": "user", "content": "sealed cell"}]
    assert env["checkpoint_highlight"] == "portable highlight"


@patch("agent_bus_store.resume_envelope._last_checkpoint_highlight")
@patch("agent_bus_store.resume_envelope.render_tape_with_harvest")
@patch("agent_bus_store.resume_envelope._find_jsonl_for_uuid", return_value=None)
def test_resume_envelope_foreign_surface_without_jsonl_is_seal_pending(
    _mock_jsonl,
    mock_render,
    _mock_highlight,
) -> None:
    """Cross-surface fixture: no local JSONL ⇒ degrade, sealed interval only."""
    from agent_bus_store.resume_envelope import build_resume_envelope

    mock_render.return_value = {
        "messages": [
            {
                "role": "assistant",
                "content": "journaled speech",
                "transcript_id": "uuid-b",
                "turn_index": 3,
            },
            {
                "role": "user",
                "content": "would-be open tail",
                "transcript_id": "uuid-b",
                "turn_index": 7,
            },
        ],
        "open_line": {
            "scope": "last_session",
            "mismatch": [],
            "open_interval": {"transcript_ids": ["uuid-b"], "turns": 4},
            "last_cp": {
                "transcript_id": "uuid-b",
                "turn_hi": 7,
            },
        },
    }
    env = build_resume_envelope("10223")
    assert env["seal_status"] == "seal_pending"
    assert env["tape_verbal"] == [{"role": "assistant", "content": "journaled speech"}]


@patch("agent_bus_store.resume_envelope.render_tape_with_harvest")
def test_resume_envelope_sealed_status_when_no_mismatch(mock_render) -> None:
    from agent_bus_store.resume_envelope import build_resume_envelope

    mock_render.return_value = {
        "messages": [],
        "verbal_messages": [{"role": "user", "content": "ok"}],
        "open_line": {"scope": "last_session", "mismatch": []},
    }
    env = build_resume_envelope("10223")
    assert env["seal_status"] == "sealed"
    assert env["checkpoint_highlight"] is None

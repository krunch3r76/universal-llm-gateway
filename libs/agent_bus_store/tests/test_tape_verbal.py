"""Verbal tape adapter — extras stripped, role/content kept, index in index[] only."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from agent_bus_store.tape_degrade import degrade_overflow_messages
from agent_bus_store.tape_harvest import render_tape_with_harvest
from agent_bus_store.tape_render import (
    _filter_messages_to_cells,
    _last_session_cells,
    render_tape,
)
from agent_bus_store.tape_verbal import (
    CORE_KEYS,
    project_role_content,
    project_role_content_list,
    tape_tail,
)
from continuity_tape.messages import strip_extras

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

_INDEX_MSG = {
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

_LEGACY_VERBAL_FIELD = "ver" + "bal_messages"


def test_verbal_adapter_strips_transcript_id_turns_at_cp_equivalents_and_bus_turn_id() -> (
    None
):
    verbal = project_role_content(_MECHANICAL)
    for key in _EXTRAS:
        assert key not in verbal
    assert set(verbal) == set(CORE_KEYS)


def test_verbal_adapter_preserves_role_and_content() -> None:
    verbal = project_role_content(_MECHANICAL)
    assert verbal["role"] == "user"
    assert verbal["content"] == "Resume from the last window."
    assert project_role_content(_MECHANICAL) is not _MECHANICAL
    assert "transcript_id" in _MECHANICAL


def test_index_role_rows_omitted_from_verbal_tape() -> None:
    mapped = project_role_content_list([_MECHANICAL, _INDEX_MSG])
    assert mapped == [{"role": "user", "content": _MECHANICAL["content"]}]


def test_last_session_scope_keeps_only_last_cp_interval() -> None:
    cells = [
        {
            "cp_ordinal": 1,
            "transcript_id": "a",
            "turn_lo": 0,
            "turn_hi": 5,
            "bus_turn_id": 10,
        },
        {
            "cp_ordinal": 2,
            "transcript_id": "a",
            "turn_lo": 5,
            "turn_hi": 12,
            "bus_turn_id": 20,
        },
        {
            "cp_ordinal": 3,
            "transcript_id": "a",
            "turn_lo": 12,
            "turn_hi": 18,
            "bus_turn_id": None,
        },
    ]
    scoped, _skipped, _foreign, _unresolved = _last_session_cells(
        cells, thread_id="10479"
    )
    assert [c["cp_ordinal"] for c in scoped] == [2, 3]
    messages = [
        {"transcript_id": "a", "turn_index": 4, "role": "user", "content": "old"},
        {"transcript_id": "a", "turn_index": 8, "role": "user", "content": "last"},
        {"transcript_id": "a", "turn_index": 15, "role": "user", "content": "open"},
    ]
    filtered = _filter_messages_to_cells(messages, scoped)
    assert [m["content"] for m in filtered] == ["last", "open"]


def test_render_tape_returns_messages_and_index_arrays() -> None:
    with (
        patch("agent_bus_store.tape_membership.list_checkpoint_turns", return_value=()),
        patch("agent_bus_store.tape_cells.list_checkpoint_turns", return_value=()),
        patch("cortex_store.db.cortex_conn") as mock_conn,
        patch("cortex_store.events_tape.agent_bus_tape_rendered"),
    ):
        conn = mock_conn.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = []
        result = render_tape(thread_id="100")
    assert "index" in result
    assert _LEGACY_VERBAL_FIELD not in result
    assert result["messages"] == []
    assert result["index"] == []
    assert result["open_line"]["message_count"] == len(result["messages"])
    assert result["open_line"]["index_count"] == len(result["index"])


@patch("agent_bus_store.tape_harvest.render_tape")
def test_harvest_forwards_include_extras_to_render_tape(mock_render) -> None:
    mock_render.return_value = {"messages": [], "index": []}
    render_tape_with_harvest(
        thread_id="1",
        budget_bytes=1000,
        harvest=False,
        include_extras=True,
        tools="marker",
    )
    assert mock_render.call_args.kwargs["include_extras"] is True
    assert mock_render.call_args.kwargs["tools"] == "marker"
    assert mock_render.call_args.kwargs["scope"] == "last_session"


def test_degrade_overflow_puts_rows_in_index_not_messages() -> None:
    messages = [
        {
            "role": "user",
            "content": "x" * 500,
            "session_id": "s1",
            "transcript_id": "t1",
            "turn_index": i,
        }
        for i in (1, 2, 3)
    ]
    kept, index_rows, truncated, _degraded = degrade_overflow_messages(
        messages,
        cells=[],
        budget_bytes=1200,
        thread_id="1",
    )
    assert truncated is True
    assert all(msg.get("role") != "index" for msg in kept)
    assert index_rows
    assert "transcript_span" in index_rows[0]
    assert "role" not in index_rows[0]


@patch(
    "agent_bus_store.resume_envelope._resume_summary_row",
    return_value=(None, None, 389),
)
@patch(
    "agent_bus_store.resume_envelope._tip_checkpoint_body",
    return_value=(389, "Highlight: portable highlight"),
)
@patch("agent_bus_store.resume_envelope.render_tape_with_harvest")
def test_resume_envelope_seal_pending_strips_unsealed_open_tail(
    mock_render,
    _mock_tip,
    _mock_summary,
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


@patch(
    "agent_bus_store.resume_envelope._resume_summary_row",
    return_value=(None, None, 389),
)
@patch(
    "agent_bus_store.resume_envelope._tip_checkpoint_body",
    return_value=(389, "Highlight: portable highlight"),
)
@patch("agent_bus_store.resume_envelope.render_tape_with_harvest")
def test_resume_envelope_journaled_speech_poured(
    mock_render,
    _mock_tip,
    _mock_summary,
) -> None:
    from agent_bus_store.resume_envelope import build_resume_envelope

    mock_render.return_value = {
        "messages": [{"role": "assistant", "content": "journaled speech"}],
        "open_line": {"scope": "last_session", "mismatch": []},
    }
    env = build_resume_envelope("10223")
    assert env["tape_verbal"] == [{"role": "assistant", "content": "journaled speech"}]


def test_strip_extras_matches_core_keys() -> None:
    assert strip_extras([_MECHANICAL]) == [
        {"role": "user", "content": "Resume from the last window."}
    ]


_MACHINE = "[Sent from machine 11111111-1111-1111-1111-111111111111]"
_SPECIMEN = [
    {"role": "user", "content": f"[t5u] {_MACHINE} Read the last turn from cursor"},
    {
        "role": "user",
        "content": f"[t6u] {_MACHINE} what is your message box number here?",
    },
    {
        "role": "user",
        "content": "[agent] A non-urgent message from House Migration 1 Harness update",
    },
    {
        "role": "user",
        "content": (
            "<timestamp>2026-10-10T16:28:00Z</timestamp><user_query>"
            "[GROK_BOT_HIDDEN_PROMPT][agent] House Migration 1: "
            "we're checkpointing you now"
            "</user_query>"
        ),
    },
]


def test_tape_tail_specimen_drops_notices_and_strips_turn_tag() -> None:
    filtered = tape_tail(_SPECIMEN, exclude_notices=True)
    assert [row["content"] for row in filtered] == [
        "Read the last turn from cursor",
        "what is your message box number here?",
    ]
    raw = tape_tail(_SPECIMEN)
    assert raw[0]["content"].startswith("[agent]")
    assert "GROK_BOT_HIDDEN_PROMPT" in raw[1]["content"]


def test_timestamp_wrapped_hidden_prompt_is_a_notice() -> None:
    verbal = [
        {
            "role": "user",
            "content": "<timestamp>t</timestamp><user_query>[GROK_BOT_HIDDEN_PROMPT] x</user_query>",
        }
    ]
    assert tape_tail(verbal, exclude_notices=True) == []


def test_bare_agent_row_dropped_assistant_kept() -> None:
    verbal = [
        {"role": "user", "content": "[agent] harness ping"},
        {"role": "assistant", "content": "[agent] quoted back"},
    ]
    assert tape_tail(verbal, exclude_notices=True) == [
        {"role": "assistant", "content": "[agent] quoted back"}
    ]


def test_mixed_cursor_row_keeps_user_query() -> None:
    verbal = [
        {
            "role": "user",
            "content": "<system_notification>tick</system_notification><user_query>implement the fix</user_query>",
        }
    ]
    assert tape_tail(verbal, exclude_notices=True) == [
        {"role": "user", "content": "implement the fix"}
    ]


def test_all_notice_pour_is_empty() -> None:
    verbal = [
        {"role": "user", "content": "[A background task finished]"},
        {"role": "user", "content": "<instructions_update>new</instructions_update>"},
    ]
    assert tape_tail(verbal, exclude_notices=True) == []

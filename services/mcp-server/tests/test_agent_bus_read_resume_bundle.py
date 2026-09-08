"""Hermetic tests for agent_bus_read resume_bundle envelope."""

from __future__ import annotations

from unittest.mock import patch

from tools._agent_bus_read import AGENT_BUS_READ_OPS
from tools.agent_bus.resume_bundle import _resume_bundle_dispatch

_BUNDLE_KEYS = frozenset(
    {
        "tape_verbal",
        "mechanical_projection_uri",
        "word_projection_uri",
        "consolidate_summary_row",
    }
)


def test_resume_bundle_registered_on_read_surface() -> None:
    assert AGENT_BUS_READ_OPS["resume_bundle"] is _resume_bundle_dispatch


def test_resume_bundle_keys_present_and_null_pointers_ok() -> None:
    tape = {
        "messages": [
            {
                "role": "user",
                "content": "resume me",
                "transcript_id": "uuid-a",
                "session_id": "s1",
                "turn_index": 1,
                "window_whole": True,
            }
        ]
    }
    with patch(
        "agent_bus_store.tape_harvest.render_tape_with_harvest",
        return_value=tape,
    ) as harvest:
        result = _resume_bundle_dispatch(thread="10223")

    harvest.assert_called_once()
    assert harvest.call_args.kwargs["harvest"] is True
    assert harvest.call_args.kwargs["format"] == "verbal"
    assert harvest.call_args.kwargs["thread_id"] == "10223"
    assert _BUNDLE_KEYS <= result.keys()
    assert result["mechanical_projection_uri"] == (
        "cortex://notes/system/threads/10223-transcript-projection.md"
    )
    assert result["word_projection_uri"] is None
    assert result["consolidate_summary_row"] is None
    assert result["tape_verbal"] == [{"role": "user", "content": "resume me"}]


def test_resume_bundle_prefers_verbal_messages_from_tape() -> None:
    tape = {
        "verbal_messages": [{"role": "assistant", "content": "poured"}],
        "messages": [
            {
                "role": "assistant",
                "content": "poured",
                "transcript_id": "uuid-b",
            }
        ],
    }
    with patch(
        "agent_bus_store.tape_harvest.render_tape_with_harvest",
        return_value=tape,
    ):
        result = _resume_bundle_dispatch(thread=10223)

    assert result["tape_verbal"] == [{"role": "assistant", "content": "poured"}]
    assert result["consolidate_summary_row"] is None


def test_resume_bundle_requires_thread() -> None:
    result = _resume_bundle_dispatch(thread="")
    assert result.get("reason") == "missing_arg"
    assert "resume_bundle" in result["error"]

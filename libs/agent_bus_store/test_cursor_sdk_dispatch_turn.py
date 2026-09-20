"""Unit tests for cursor-sdk dispatch turn terminal classification (AC-SR-12)."""

from __future__ import annotations

from agent_bus_store.cursor_sdk_dispatch_turn import (
    is_cursor_sdk_dispatch_terminal_subject,
    sdk_terminal_closeout_turn,
)
from services.git_integration_worker.cursor_sdk_closeout_subject import (
    build_sdk_closeout_subject,
)
from services.git_integration_worker.models.cursor_api import CursorDispatchRequest


def test_parked_dispatch_subject_is_not_terminal() -> None:
    subject = "cursor-sdk dispatch disp-1 PARKED (for GIW restart intent-9)"
    assert not is_cursor_sdk_dispatch_terminal_subject(subject)


def test_resumed_dispatch_subject_is_not_terminal() -> None:
    subject = "cursor-sdk dispatch child-1 RESUMED (resume_of parent-1, restart i9)"
    assert not is_cursor_sdk_dispatch_terminal_subject(subject)


def test_completed_dispatch_subject_is_terminal() -> None:
    assert is_cursor_sdk_dispatch_terminal_subject(
        "cursor-sdk dispatch disp-1 COMPLETED"
    )


def test_bare_closeout_subject_is_not_terminal() -> None:
    assert not is_cursor_sdk_dispatch_terminal_subject("CLOSEOUT — task complete")


def test_producer_closeout_subject_is_terminal() -> None:
    req = CursorDispatchRequest(
        thread_id="11788",
        model="cursor/composer-2.5",
        dispatch_id="893994c82a61-75be8122",
        execution_id="exec-893994c82a61",
        message="conductor hop",
        caller_agent="conductor-hop",
    )
    subject = build_sdk_closeout_subject(req, contract="conductor")
    assert subject.startswith("cursor-sdk CLOSEOUT 893994c82a61-75be8122")
    assert is_cursor_sdk_dispatch_terminal_subject(subject)


def test_sdk_terminal_closeout_turn_skips_parked_picks_child() -> None:
    turns = [
        {
            "from_agent": "cursor-sdk",
            "subject": "cursor-sdk dispatch child-2 COMPLETED",
            "turn_number": 4,
        },
        {
            "from_agent": "cursor-sdk",
            "subject": "cursor-sdk dispatch parent-1 PARKED (for GIW restart x)",
            "turn_number": 3,
        },
    ]
    terminal = sdk_terminal_closeout_turn(turns)
    assert terminal is not None
    assert "COMPLETED" in terminal["subject"]


def test_sdk_terminal_closeout_turn_parked_only_returns_none() -> None:
    turns = [
        {
            "from_agent": "cursor-sdk",
            "subject": "cursor-sdk dispatch parent-1 PARKED (for GIW restart x)",
            "turn_number": 2,
        },
    ]
    assert sdk_terminal_closeout_turn(turns) is None

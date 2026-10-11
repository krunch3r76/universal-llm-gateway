"""Branch coverage for unread-turn classification."""

from __future__ import annotations

import pytest
from agent_bus_store.bus_notice import classify_unread_turn

pytestmark = pytest.mark.offline

_SEAT = {"cursor", "web", "claude-cursor"}


def _cls(**kwargs: str) -> str:
    payload = {
        "from_agent": "grok-bot-hm1",
        "to_agent": "cursor",
        "subject": "hello",
        "body": "plain",
        "seat_slugs": _SEAT,
    }
    payload.update(kwargs)
    return classify_unread_turn(**payload)  # type: ignore[arg-type]


def test_not_addressed_when_recipient_outside_seat() -> None:
    assert _cls(to_agent="grok-bot-hm1") == "not_addressed"


def test_broadcast_all_and_team_are_addressed() -> None:
    assert _cls(to_agent="all", subject="FACT: shared") == "memo"
    assert _cls(to_agent="team", subject="FACT: shared") == "memo"


def test_same_seat_memo_and_note_are_memos() -> None:
    assert (
        _cls(from_agent="cursor", to_agent="cursor", subject="NOTE for successor")
        == "memo"
    )
    assert _cls(from_agent="web", to_agent="cursor", subject="MEMO: handoff") == "memo"


def test_own_outgoing_including_generate_admitted() -> None:
    assert (
        _cls(
            from_agent="cursor",
            to_agent="web-anthropic",
            subject="cursor-sdk generate admitted",
        )
        == "not_addressed"
    )
    assert (
        _cls(
            from_agent="cursor",
            to_agent="cursor",
            subject="cursor-sdk generate admitted",
        )
        == "own"
    )


def test_self_post_notice_when_not_own_seat() -> None:
    assert _cls(from_agent="all", to_agent="all", subject="status") == "notice"


def test_pipeline_senders_and_cursor_sdk_prefix_are_notices() -> None:
    assert _cls(from_agent="conductor-hop", subject="park-harvest wake") == "notice"
    assert _cls(from_agent="dispatch", subject="admitted") == "notice"
    assert (
        _cls(from_agent="closeout-memo", subject="closeout memo — undelivered")
        == "notice"
    )
    assert _cls(from_agent="cursor-sdk", subject="cursor-sdk CLOSEOUT lane") == "notice"
    assert _cls(from_agent="cursor-sdk:dispatch:abc", subject="admitted") == "notice"


def test_notice_subject_prefixes_and_marker() -> None:
    assert _cls(subject="LANE CLOSEOUT 16356") == "notice"
    assert _cls(subject="lane abandoned — worker") == "notice"
    assert _cls(subject="Re: generate admitted on lane") == "notice"


def test_hidden_prompt_body_is_notice() -> None:
    assert _cls(body="[GROK_BOT_HIDDEN_PROMPT] boot the house") == "notice"


def test_ruling_fact_and_free_text_are_memos() -> None:
    assert _cls(subject="RULING|rerun") == "memo"
    assert _cls(subject="FACT: the tree is dirty") == "memo"
    assert _cls(subject="please re-run X", body="do the thing") == "memo"

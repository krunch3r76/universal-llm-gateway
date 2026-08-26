"""Unit tests for horizon probe classifier and seated-authorship oracle."""

from __future__ import annotations

from systems.frontier_consult.cdp_horizon_probe import (
    SEATED_CDP_FROM_AGENT,
    seated_authorship_hit,
)


def test_seated_authorship_ignores_cursor_auto_presence() -> None:
    turns = [
        {"from_agent": "cursor-auto", "subject": "WAKE", "body": "heartbeat"},
        {"from_agent": "cursor-auto", "subject": "admit", "body": "ok"},
    ]
    assert seated_authorship_hit(turns) is False


def test_seated_authorship_ignores_substrate_failed_subject() -> None:
    turns = [
        {
            "from_agent": SEATED_CDP_FROM_AGENT,
            "subject": "cdp FAILED — a3ba868b",
            "body": "# CDP generate FAILED (opus-5-high)",
        }
    ]
    assert seated_authorship_hit(turns) is False


def test_seated_authorship_ignores_substrate_unverified_subject() -> None:
    turns = [
        {
            "from_agent": SEATED_CDP_FROM_AGENT,
            "subject": "cdp UNVERIFIED — 3f492a7c",
            "body": "# CDP generate UNVERIFIED (fable-5-high)",
        }
    ]
    assert seated_authorship_hit(turns) is False


def test_seated_authorship_counts_seated_cse_speech() -> None:
    turns = [
        {
            "from_agent": SEATED_CDP_FROM_AGENT,
            "subject": "CONTRADICTION to turn 28",
            "body": "this seat is live fe6aabca49154c219b251b2f518ec725",
        }
    ]
    assert seated_authorship_hit(turns) is True
    assert (
        seated_authorship_hit(
            turns, successor_birth_id="fe6aabca49154c219b251b2f518ec725"
        )
        is True
    )
    assert seated_authorship_hit(turns, successor_birth_id="other-birth") is False

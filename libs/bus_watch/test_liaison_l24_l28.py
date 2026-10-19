"""Unit tests for L24 unrelayed K43 replies and L28 dead-letter maestro turns."""

from __future__ import annotations

from bus_watch.liaison_l24_l28 import (
    check_dead_letter_maestro_turns,
    check_unrelayed_k43_replies,
)

_NOW = 1_700_000_000.0
_FA2230 = "fa2230f8-0000-4000-8000-000000000001"
_C1A0CF = "c1a0cf01-0000-4000-8000-000000000002"


def _iso(offset_s: float) -> str:
    from datetime import UTC, datetime

    return (
        datetime.fromtimestamp(_NOW - offset_s, tz=UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def test_l24_unrelayed_fa2230f8_after_sixteen_minutes() -> None:
    turns = [
        {
            "turn_number": 974,
            "subject": "cdp reply — fa2230f8",
            "from": "web-anthropic",
            "execution_ids": [_FA2230],
            "created_at": _iso(16 * 60),
            "body": f"execution_id: `{_FA2230}`\nVERDICT: REQUEST_CHANGES\n",
        },
        {
            "turn_number": 980,
            "subject": "relay other",
            "from": "cursor",
            "created_at": _iso(10 * 60),
            "body": "no verdict here",
        },
    ]
    alerts = check_unrelayed_k43_replies(turns, _NOW)
    assert len(alerts) == 1
    assert alerts[0]["kind"] == "unrelayed_k43_reply"
    assert alerts[0]["turn_number"] == 974
    assert alerts[0]["execution_id"] == _FA2230


def test_l24_c1a0cf01_relayed_not_flagged() -> None:
    turns = [
        {
            "turn_number": 977,
            "subject": "cdp reply — c1a0cf01",
            "from": "web-anthropic",
            "execution_ids": [_C1A0CF],
            "created_at": _iso(20 * 60),
            "body": "REQUEST_CHANGES details",
        },
        {
            "turn_number": 978,
            "subject": "VERDICT relay",
            "from": "cursor",
            "created_at": _iso(18 * 60),
            "body": f"VERDICT: APPROVE execution_id={_C1A0CF}\n",
        },
    ]
    assert check_unrelayed_k43_replies(turns, _NOW) == []


def test_l24_young_reply_not_flagged() -> None:
    turns = [
        {
            "turn_number": 1,
            "subject": "cdp reply — fa2230f8",
            "from": "web-anthropic",
            "execution_ids": [_FA2230],
            "created_at": _iso(14 * 60),
            "body": "pending",
        },
    ]
    assert check_unrelayed_k43_replies(turns, _NOW) == []


def test_l28_lock_present_no_alert() -> None:
    turns = [
        {
            "turn_number": 6,
            "subject": "AMEND| cost plan",
            "from": "maestro",
            "read_at": None,
            "created_at": _iso(31 * 60),
        },
    ]
    locks = {
        "18374": {
            "holder": "ide:tab-1",
            "claimed_at": _iso(60),
            "expires_at": _iso(-3600),
        }
    }
    alerts, _state = check_dead_letter_maestro_turns({"18374": turns}, locks, _NOW)
    assert alerts == []


def test_l28_lock_absent_thirty_one_minutes_one_alert() -> None:
    turns = [
        {
            "turn_number": 6,
            "subject": "AMEND| batch-2",
            "from": "maestro",
            "read_at": None,
            "created_at": _iso(31 * 60),
        },
    ]
    alerts, state = check_dead_letter_maestro_turns(
        {"18374": turns}, {"18374": {}}, _NOW
    )
    assert len(alerts) == 1
    assert alerts[0]["alert_subject_prefix"] == "BLOCKED|"
    assert alerts[0]["thread"] == "18374"
    assert alerts[0]["turn_number"] == 6


def test_l28_twenty_nine_minutes_no_alert() -> None:
    turns = [
        {
            "turn_number": 7,
            "subject": "REQUEST| follow-up",
            "from": "maestro",
            "read_at": None,
            "created_at": _iso(29 * 60),
        },
    ]
    alerts, _ = check_dead_letter_maestro_turns({"18374": turns}, {"18374": {}}, _NOW)
    assert alerts == []


def test_l28_idempotent_second_tick() -> None:
    turns = [
        {
            "turn_number": 6,
            "subject": "AMEND| batch-2",
            "from": "maestro",
            "read_at": None,
            "created_at": _iso(31 * 60),
        },
    ]
    alerted: dict[str, list[str]] = {}
    first, alerted = check_dead_letter_maestro_turns(
        {"18374": turns}, {"18374": {}}, _NOW, alerted_state=alerted
    )
    second, alerted = check_dead_letter_maestro_turns(
        {"18374": turns}, {"18374": {}}, _NOW, alerted_state=alerted
    )
    assert len(first) == 1
    assert second == []

"""Tests for CONSUMED turn grammar and consumption-missing markers."""

from __future__ import annotations

import pytest

from receipts.consumption import (
    CONSUMED_TYPE_RE,
    RULING_TYPE_RES,
    consumption_missing_marker,
    find_unconsumed_rulings,
    parse_consumed,
)


def _first_type_matches(body: str, pattern) -> bool:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("TYPE:"):
            return pattern.search(stripped) is not None
    return False


def test_consumed_type_re_first_line_only() -> None:
    body = (
        "TYPE: NOTE\n"
        "Later prose mentions TYPE: CONSUMED but this is not the envelope.\n"
    )
    assert not _first_type_matches(body, CONSUMED_TYPE_RE)
    assert _first_type_matches("TYPE: CONSUMED\nconsumed_turn: 1", CONSUMED_TYPE_RE)


def test_ruling_type_re_first_line_only() -> None:
    body = (
        "TYPE: NOTE\n"
        "See TYPE: RULING from turn 3 for context.\n"
    )
    assert not _first_type_matches(body, RULING_TYPE_RES)
    assert _first_type_matches("TYPE: DIRECTIVE\nbind: x", RULING_TYPE_RES)
    assert _first_type_matches("TYPE: RULING\nbind: x", RULING_TYPE_RES)


def test_ruling_body_with_later_ruling_token_not_classified() -> None:
    turns = [
        {
            "turn_number": 1,
            "body": "TYPE: NOTE\nFollow TYPE: RULING on turn 9.\n",
        },
        {"turn_number": 2, "body": "TYPE: DIRECTIVE\nbind: a\n"},
    ]
    assert find_unconsumed_rulings(turns) == [2]


def test_parse_consumed_valid() -> None:
    body = (
        "TYPE: CONSUMED\n"
        "consumed_turn: 12\n"
        "disposition: acted\n"
        "by: web-anthropic\n"
    )
    assert parse_consumed(body) == {
        "consumed_turn": 12,
        "disposition": "acted",
        "by": "web-anthropic",
    }


@pytest.mark.parametrize(
    "body",
    [
        "TYPE: CONSUMED\n",
        "TYPE: CONSUMED\nconsumed_turn: x\n",
        "TYPE: CONSUMED\nconsumed_turn: 1\ndisposition: noop\nby: a\n",
        "TYPE: NOTE\nconsumed_turn: 1\ndisposition: acted\nby: a\n",
        "",
    ],
)
def test_parse_consumed_malformed_returns_none(body: str) -> None:
    assert parse_consumed(body) is None


def test_find_unconsumed_ruling_consumed_pair_empty() -> None:
    turns = [
        {"turn_number": 5, "body": "TYPE: RULING\nrule: one\n"},
        {
            "turn_number": 6,
            "body": (
                "TYPE: CONSUMED\n"
                "consumed_turn: 5\n"
                "disposition: deferred\n"
                "by: cursor-sdk\n"
            ),
        },
    ]
    assert find_unconsumed_rulings(turns) == []


def test_find_unconsumed_ruling_alone() -> None:
    turns = [{"turn_number": 7, "body": "TYPE: DIRECTIVE\nbind: y\n"}]
    assert find_unconsumed_rulings(turns) == [7]


def test_find_unconsumed_consumed_wrong_turn_still_open() -> None:
    turns = [
        {"turn_number": 8, "body": "TYPE: RULING\nrule: z\n"},
        {
            "turn_number": 9,
            "body": (
                "TYPE: CONSUMED\n"
                "consumed_turn: 99\n"
                "disposition: acted\n"
                "by: lead\n"
            ),
        },
    ]
    assert find_unconsumed_rulings(turns) == [8]


def test_find_unconsumed_no_rulings_empty() -> None:
    turns = [{"turn_number": 1, "body": "TYPE: NOTE\nhello\n"}]
    assert find_unconsumed_rulings(turns) == []


def test_find_unconsumed_never_raises_on_bad_turns() -> None:
    assert find_unconsumed_rulings([None, "x", {"body": "TYPE: RULING"}]) == []


def test_consumption_missing_marker_format() -> None:
    assert consumption_missing_marker([15, 12]) == "CONSUMPTION_MISSING:12,15"


def test_consumption_missing_marker_empty_none() -> None:
    assert consumption_missing_marker([]) is None

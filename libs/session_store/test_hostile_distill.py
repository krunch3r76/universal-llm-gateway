"""Adversarial Index-line tests (DIRECTIVE-2 AC3 / spec §4 rule (e)).

A distilled Index line is emitted unfenced into ``## Index``. It must therefore
stay one line, stay inside the length bound, and never itself parse as an ATX
heading or a fence — whatever the turn body contains.
"""

from __future__ import annotations

import re

import pytest

from session_store.distill import distill_index_line

MAX_CHARS = 120
PREFIX = "0007 user: "

_ATX_RE = re.compile(r"^[ \t]*#{1,6}[ \t]+")
_MD_LIST_FENCE_RE = re.compile(r"^[ \t]*(```|~~~)")

# Every code point str.splitlines() treats as a line break.
_LINE_BREAKERS = (
    "\n",
    "\r",
    "\r\n",
    "\v",
    "\f",
    "\x1c",
    "\x1d",
    "\x1e",
    "\x85",
    "\u2028",
    "\u2029",
)


def _bodies() -> dict[str, str]:
    cases = {
        "newlines": "first line\nsecond line\nthird line",
        "lone_cr": "before\rafter",
        "crlf": "before\r\nafter",
        "long_first_line": "L" * 300 + "\nsecond line",
        "long_single_line": "L" * 500,
        "empty": "",
        "whitespace_only": "  \n\t\r\n  ",
        "leading_turn_heading": "## Turn 0007 — assistant\nbody",
        "fence_line": "```\ncode\n```",
        "tilde_fence_line": "~~~\ncode\n~~~",
        "index_none_sentinel": "(none)",
        "nbsp_only": "\u00a0\u00a0",
        "astral": "\U0001f600" * 300,
        "combined": "## Turn 0007 — assistant\r\n" + "X" * 200 + "\rtail",
    }
    for i, brk in enumerate(_LINE_BREAKERS):
        cases[f"breaker_{i}"] = f"head{brk}tail"
        cases[f"breaker_after_long_first_{i}"] = ("Q" * 300) + brk + "tail"
        cases[f"breaker_leading_{i}"] = brk + "tail"
    return cases


_CASES = sorted(_bodies().items())


@pytest.mark.parametrize(("name", "body"), _CASES)
def test_index_line_is_one_line_within_bound(name: str, body: str) -> None:
    line = distill_index_line(7, "user", body, max_chars=MAX_CHARS)
    assert line.splitlines() == [line], name
    assert len(line) <= MAX_CHARS, name
    assert line.startswith(PREFIX), name


@pytest.mark.parametrize(("name", "body"), _CASES)
def test_index_line_cannot_parse_as_heading_or_fence(name: str, body: str) -> None:
    line = distill_index_line(7, "user", body, max_chars=MAX_CHARS)
    assert not _ATX_RE.match(line), name
    assert not _MD_LIST_FENCE_RE.match(line), name
    assert line != "(none)", name


@pytest.mark.parametrize("max_chars", [1, 5, 10, 11, 12, 13, 40, 119, 120, 400])
def test_bound_and_single_line_hold_at_every_max_chars(max_chars: int) -> None:
    for name, body in _CASES:
        line = distill_index_line(7, "user", body, max_chars=max_chars)
        assert len(line) <= max_chars, (name, max_chars)
        assert line.splitlines() == [line], (name, max_chars)


def test_long_first_line_truncates_to_the_bound_with_ellipsis() -> None:
    line = distill_index_line(7, "user", "L" * 300 + "\ntail", max_chars=MAX_CHARS)
    assert len(line) == MAX_CHARS
    assert line.endswith("…")
    assert "tail" not in line


def test_short_body_is_not_truncated() -> None:
    line = distill_index_line(7, "user", "asks about\rthe ledger", max_chars=MAX_CHARS)
    assert line == f"{PREFIX}asks about the ledger"

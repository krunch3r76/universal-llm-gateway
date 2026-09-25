"""Table-driven tests for unified review verdict grammar."""

from __future__ import annotations

import pytest

from review_verdict import (
    VerdictAction,
    format_canonical_gate6_block,
    format_canonical_merits_line,
    gate6_affirmative_disposition,
    parse_any_review_body,
    parse_gate6_markdown,
    parse_merits_line,
)

_MATRIX_TOKENS = (
    "RATIFY",
    "RATIFY-WITH-CONDITIONS",
    "RATIFY_WITH_CONDITIONS",
    "ADMIT",
    "ADMIT_WITH_AMENDMENTS",
    "RETURN",
    "SCOPE-DRIFT",
    "REJECT",
)

_EXPECTED_ACTION = {
    "RATIFY": VerdictAction.ADVANCE,
    "RATIFY-WITH-CONDITIONS": VerdictAction.AMENDMENTS_REQUIRED,
    "RATIFY_WITH_CONDITIONS": VerdictAction.AMENDMENTS_REQUIRED,
    "ADMIT": VerdictAction.ADVANCE,
    "ADMIT_WITH_AMENDMENTS": VerdictAction.AMENDMENTS_REQUIRED,
    "RETURN": VerdictAction.BLOCKED,
    "SCOPE-DRIFT": VerdictAction.BLOCKED,
    "REJECT": VerdictAction.BLOCKED,
}

_R1_TEMPLATE_BODY = """# Pre-land review — sample

**Branch:** `cursor-sdk/lane-12686`
**Spec sha256:** spec_sha256:abc

## Scope check

Scope matches the bound G3 spec and lane diff.

## Merits

Merits: RATIFY

## Findings

(optional bullets)

## Verdict

**Verdict:** **RATIFY**
"""


def _merits_plain(token: str) -> str:
    return f"Merits: {token}"


def _merits_bold(token: str) -> str:
    return f"**Merits:** {token}"


def _gate6_bold(token: str) -> str:
    return format_canonical_gate6_block(
        token.replace("-", "_").replace("SCOPE_DRIFT", "SCOPE-DRIFT")
    )


def _gate6_heading(token: str) -> str:
    display = token
    if token == "RATIFY_WITH_CONDITIONS":
        display = "RATIFY-WITH-CONDITIONS"
    return f"## Verdict: **{display}**"


def _subject_fallback(token: str) -> str:
    return f"G6 review — {token}"


@pytest.mark.offline
@pytest.mark.parametrize("token", _MATRIX_TOKENS)
def test_merits_plain_wrapper(token: str) -> None:
    parsed = parse_merits_line(_merits_plain(token))
    assert parsed.action is _EXPECTED_ACTION[token]


@pytest.mark.offline
@pytest.mark.parametrize("token", _MATRIX_TOKENS)
def test_merits_bold_wrapper(token: str) -> None:
    parsed = parse_merits_line(_merits_bold(token))
    assert parsed.action is _EXPECTED_ACTION[token]


@pytest.mark.offline
@pytest.mark.parametrize("token", _MATRIX_TOKENS)
def test_gate6_bold_wrapper(token: str) -> None:
    if token == "REJECT":
        body = "**Verdict:** **REJECT — scope**"
    else:
        body = _gate6_bold(token)
    parsed = parse_gate6_markdown(body)
    assert parsed.action is _EXPECTED_ACTION[token]


@pytest.mark.offline
@pytest.mark.parametrize("token", _MATRIX_TOKENS)
def test_gate6_heading_wrapper(token: str) -> None:
    if token == "REJECT":
        body = "## Verdict: **REJECT**"
    else:
        body = _gate6_heading(token)
    parsed = parse_gate6_markdown(body)
    assert parsed.action is _EXPECTED_ACTION[token]


@pytest.mark.offline
@pytest.mark.parametrize("token", _MATRIX_TOKENS)
def test_subject_fallback_wrapper(token: str) -> None:
    parsed = parse_merits_line(_subject_fallback(token))
    assert parsed.action is _EXPECTED_ACTION[token]


@pytest.mark.offline
def test_scope_check_guard_does_not_mask_missing_merits() -> None:
    body = "Scope check: RATIFY — bounded.\nNo merits here.\n"
    parsed = parse_merits_line(body)
    assert parsed.token is None
    assert parsed.action is VerdictAction.BLOCKED


@pytest.mark.offline
def test_r1_template_merits_and_gate6_agree() -> None:
    merits = parse_merits_line(_R1_TEMPLATE_BODY)
    gate6 = parse_gate6_markdown(_R1_TEMPLATE_BODY)
    assert merits.action is VerdictAction.ADVANCE
    assert gate6.action is VerdictAction.ADVANCE
    assert parse_any_review_body(_R1_TEMPLATE_BODY).action is VerdictAction.ADVANCE


@pytest.mark.offline
def test_format_canonical_merits_line_underscore() -> None:
    assert format_canonical_merits_line("ratify-with-conditions") == (
        "Merits: RATIFY_WITH_CONDITIONS"
    )


_A1_NON_AFFIRMATIVE_RATIFY_PROSE = (
    "RATIFY WITH CONDITIONS",
    "RATIFY — conditional on AC3",
    "RATIFY (with conditions)",
)


@pytest.mark.offline
@pytest.mark.parametrize("token_prose", _A1_NON_AFFIRMATIVE_RATIFY_PROSE)
def test_a1_whole_token_not_affirmative_ratify(token_prose: str) -> None:
    body = f"**Verdict:** **{token_prose}**"
    parsed = parse_gate6_markdown(body)
    assert parsed.action is VerdictAction.BLOCKED
    assert parsed.reason == "unknown_verdict"
    assert gate6_affirmative_disposition(body) is False


@pytest.mark.offline
def test_a2_blocked_verdict_wins_over_ratify() -> None:
    body = "\n".join(
        [
            "**Verdict:** **REJECT**",
            "",
            "## Verdict: **RATIFY**",
        ]
    )
    parsed = parse_gate6_markdown(body)
    assert parsed.action is VerdictAction.BLOCKED
    assert parsed.token == "REJECT"
    assert gate6_affirmative_disposition(body) is False

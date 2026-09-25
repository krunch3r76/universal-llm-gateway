"""Focused gate-6 verdict disposition tests (shared review_verdict grammar)."""

from __future__ import annotations

import pytest
from review_verdict import gate6_affirmative_disposition

from implement_admission.implement_ready_gate6_resolve import (
    resolve_gate6_ratification,
)

_SPEC_HASH = "spec_sha256:abc"
_SPEC = "cortex://notes/system/specs/sample.md"


def _fetch(turns: dict[tuple[str, int], dict[str, object]]) -> object:
    def _inner(thread: str, turn_number: int) -> dict[str, object] | None:
        return turns.get((thread, turn_number))

    return _inner


def _body(verdict_line: str) -> str:
    return "\n".join(
        [
            verdict_line,
            "",
            _SPEC_HASH,
            "",
            "FILE_EVIDENCE_PATHS:",
            "cortex://notes/system/specs/sample.md",
        ]
    )


@pytest.mark.offline
@pytest.mark.parametrize(
    ("verdict_line", "affirmative"),
    [
        ("## Verdict: **RATIFY**", True),
        ("**Verdict:** **RATIFY**", True),
        ("## Verdict: **RATIFY-WITH-CONDITIONS**", False),
        ("**Verdict:** **RATIFY_WITH_CONDITIONS**", False),
        ("## Verdict: **REJECT**", False),
        ("## Verdict: **RETURN**", False),
        ("**Verdict:** **RATIFY WITH CONDITIONS**", False),
        ("**Verdict:** **RATIFY — conditional on AC3**", False),
        ("**Verdict:** **RATIFY (with conditions)**", False),
        (
            "\n".join(
                [
                    "**Verdict:** **REJECT**",
                    "",
                    "## Verdict: **RATIFY**",
                ]
            ),
            False,
        ),
    ],
)
def test_gate6_affirmative_only_advance(verdict_line: str, affirmative: bool) -> None:
    body = _body(verdict_line)
    assert gate6_affirmative_disposition(body) is affirmative


@pytest.mark.offline
def test_gate6_missing_verdict_fails_closed() -> None:
    assert gate6_affirmative_disposition(_body("no verdict")) is False


@pytest.mark.offline
def test_resolve_gate6_ratify_with_conditions_not_ratified() -> None:
    outcome = resolve_gate6_ratification(
        todo_attrs={"gate6_ratification_uri": "agent-bus:1#turn-1"},
        implement_ready_assertion={
            "entity_id": "todo:x",
            "evidence_uris": [_SPEC, _SPEC_HASH],
        },
        spec_hash_uri=_SPEC_HASH,
        fetch_bus_turn=_fetch(
            {
                ("1", 1): {
                    "body": _body("## Verdict: **RATIFY-WITH-CONDITIONS**"),
                    "from": "reviewer",
                }
            }
        ),
    )
    assert not outcome.ratified
    assert outcome.reason is not None and "affirmative verdict" in outcome.reason

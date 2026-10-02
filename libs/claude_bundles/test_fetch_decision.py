"""Receipt grammar for a staged fetch that was not inlined."""

from __future__ import annotations

import hashlib

import pytest

from claude_bundles.fetch_decision import (
    arrival_bind_failure,
    decision_lines,
    format_fetch_decision,
    parse_fetch_decisions,
    success_condition_line,
)

pytestmark = pytest.mark.offline

_POINTER_ONLY = (
    "## This hop (read first)\n"
    "- lane: agent-bus:12286\n"
    "- runbook: runbook:maestro-loop\n"
)


def test_pointer_only_block_is_receipt_absent() -> None:
    assert arrival_bind_failure(_POINTER_ONLY) == "success_condition_absent"


def test_skipped_is_a_receipt_and_not_in_force() -> None:
    block = decision_lines()
    assert arrival_bind_failure(block) == "receipt_unresolved"
    parsed = {row.ref: row for row in parse_fetch_decisions(block)}
    assert parsed["runbook:maestro-loop"].state == "skipped"
    assert parsed["runbook:maestro-loop"].reason == "not_in_context"


def test_resolved_hash_is_reachable_not_in_force() -> None:
    body = "step list\n"
    block = decision_lines(resolved_bodies={"runbook:maestro-loop": body})
    parsed = {row.ref: row for row in parse_fetch_decisions(block)}
    row = parsed["runbook:maestro-loop"]
    assert row.state == "resolved"
    assert row.sha256 == hashlib.sha256(body.encode()).hexdigest()
    assert row.nbytes == len(body.encode())
    assert arrival_bind_failure(block) is None


def test_missing_success_condition_fails_even_with_receipts() -> None:
    line = format_fetch_decision(
        "runbook:maestro-loop", state="skipped", reason="not_in_context"
    )
    other = format_fetch_decision(
        "skill:retrieval-before-authoring",
        state="skipped",
        reason="not_in_context",
    )
    assert arrival_bind_failure(f"{line}\n{other}\n") == "success_condition_absent"


def test_skipped_rejects_a_free_reason() -> None:
    with pytest.raises(ValueError):
        format_fetch_decision(
            "runbook:maestro-loop", state="skipped", reason="not read"
        )


def test_success_condition_line_is_short_when_refuse_present() -> None:
    a = success_condition_line("first  refuse  body")
    b = success_condition_line("second refuse body")
    assert a == b
    assert a.startswith("- success-condition:")
    assert "contract=conductor" in a
    assert "first" not in a
    assert "second" not in a


def test_success_condition_does_not_embed_refuse_body() -> None:
    refuse = "Never send contract=none on hop birth."
    line = success_condition_line(refuse)
    assert "contract=none" not in line
    assert "Refuse bullets" in line


def test_arrival_bind_failure_rejects_skipped_required_ref() -> None:
    both_skipped = decision_lines(
        skip_reasons={
            "runbook:maestro-loop": "not_in_context",
            "skill:retrieval-before-authoring": "not_in_context",
        }
    )
    assert arrival_bind_failure(both_skipped) == "receipt_unresolved"
    mixed = decision_lines(
        resolved_bodies={"runbook:maestro-loop": "x"},
        skip_reasons={"skill:retrieval-before-authoring": "not_resolvable_by_composer"},
    )
    assert arrival_bind_failure(mixed) is None

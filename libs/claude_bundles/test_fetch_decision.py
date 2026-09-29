"""Receipt grammar for a staged fetch that was not inlined."""

from __future__ import annotations

import hashlib

import pytest

from claude_bundles.fetch_decision import (
    arrival_bind_failure,
    decision_lines,
    format_fetch_decision,
    parse_fetch_decisions,
    step_list_in_force,
)
from claude_bundles.operator_proxy_hop_status import ensure_hop_status_first

pytestmark = pytest.mark.offline

_POINTER_ONLY = (
    "## This hop (read first)\n"
    "- lane: agent-bus:12286\n"
    "- runbook: runbook:maestro-loop\n"
)


def test_pointer_only_block_is_receipt_absent() -> None:
    assert arrival_bind_failure(_POINTER_ONLY) == "success_condition_absent"
    grafted = ensure_hop_status_first(_POINTER_ONLY)
    assert arrival_bind_failure(grafted) is None
    assert "- runbook: runbook:maestro-loop" in grafted
    assert not step_list_in_force(grafted, "runbook:maestro-loop")


def test_skipped_is_a_receipt_and_not_in_force() -> None:
    block = decision_lines()
    assert arrival_bind_failure(block) is None
    assert not step_list_in_force(block, "runbook:maestro-loop")
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
    assert not step_list_in_force(block, "runbook:maestro-loop")
    assert step_list_in_force(
        decision_lines(in_context_refs=("runbook:maestro-loop",)),
        "runbook:maestro-loop",
    )


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

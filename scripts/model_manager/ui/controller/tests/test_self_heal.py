"""Offline tests for charter-runner self-heal (R-admit A1/A2 round-trip)."""

from __future__ import annotations

from scripts.model_manager.ui.controller.charter_runner.checkpoint_schema import (
    parse_checkpoint,
)
from scripts.model_manager.ui.controller.charter_runner.self_heal import (
    CHECKPOINT_MISSING,
    build_self_heal_checkpoint,
    incomplete_window_reason,
    window_terminal_after,
)


_PRIOR = """\
# CHECKPOINT wave 2

## Steps
1. [x] Recon complete
2. [~] Densify G2
3. [!] Blocked probe
4. [ ] Next gated

## In-flight / WIP
none

## Next-pickup
- G2 — densify implement spec
- CONSULT_PENDING consult_role: r_admit

## BLOCKED
None.

## Scoreboard URI
cortex://notes/system/threads/5555-charter-scoreboard.md

— RESUME (any seat, no command): load agent-bus-discipline → this is the latest CHECKPOINT.
"""


def test_build_self_heal_round_trip_preserves_gated_pickup() -> None:
    prior = parse_checkpoint(_PRIOR)
    assert prior.next_pickup_gated is True
    _subject, body = build_self_heal_checkpoint(
        prior=prior,
        window_index=2,
        worker_thread="5739",
        reason=CHECKPOINT_MISSING,
    )
    echo = parse_checkpoint(body)
    assert echo.next_pickup == prior.next_pickup
    assert echo.next_pickup_gated is True
    assert echo.blocked is False
    statuses = {s.title: s.status for s in echo.steps}
    assert statuses["Recon complete"] == "done"
    assert statuses["Densify G2"] == "in_progress"
    assert statuses["Blocked probe"] == "blocked"
    assert statuses["Next gated"] == "pending"
    assert "agent-bus:5739" in body
    assert "machine self-heal" in body


def test_build_self_heal_carries_blocked() -> None:
    # Bare BLOCKED line is what _detect_blocked keys on (section body alone is not).
    body = _PRIOR.replace(
        "## BLOCKED\nNone.",
        "## BLOCKED\nBLOCKED — revise_cap_exhausted\n",
    )
    prior = parse_checkpoint(body)
    assert prior.blocked is True
    _subject, heal_body = build_self_heal_checkpoint(
        prior=prior,
        window_index=1,
        worker_thread="",
        reason=CHECKPOINT_MISSING,
    )
    echo = parse_checkpoint(heal_body)
    assert echo.blocked is True


def test_window_terminal_after_consult_pending() -> None:
    turns = [
        {"turn_number": 5, "subject": "WIP charter-runner window 3"},
        {
            "turn_number": 8,
            "subject": "CONSULT_PENDING — G3 R-admit",
            "body": "consult_role: r_admit",
        },
    ]
    assert window_terminal_after(turns, 5) == "CONSULT_PENDING"
    reason, terminal = incomplete_window_reason(
        root_turns=turns,
        admission_turn=turns[0],
        worker_status="complete",
    )
    assert reason is None
    assert terminal == "CONSULT_PENDING"


def test_incomplete_window_reason_checkpoint_missing() -> None:
    turns = [
        {"turn_number": 5, "subject": "WIP charter-runner window 3"},
        {"turn_number": 6, "subject": "note — still working"},
    ]
    reason, terminal = incomplete_window_reason(
        root_turns=turns,
        admission_turn=turns[0],
        worker_status="complete",
    )
    assert reason == CHECKPOINT_MISSING
    assert terminal is None

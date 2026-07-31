"""Unit tests for orchestration charter birth lint."""

from __future__ import annotations

from agent_bus_store.checkpoint_charter_lint import (
    lint_orchestration_charter_binding,
    orchestration_charter_advisory,
    requires_orchestration_charter_binding,
)

_VALID_BIRTH = """\
TYPE: CHECKPOINT

## Anchor
- Thread: agent-bus:9999
- Objective: Ship the orchestration birth gate

## State
**Primary OPEN:** G1 — birth gate

## Next pickup
1. G1 — implement lint · executor=cursor/composer-2.5

## Scoreboard URI
cortex://notes/system/threads/9999-charter-scoreboard.md
"""


def test_requires_binding_on_birth_checkpoint() -> None:
    assert requires_orchestration_charter_binding(
        subject="CHECKPOINT — birth",
        thread_tags=[],
        supersedes_turn=None,
    )


def test_requires_binding_on_bootstrap_structural() -> None:
    assert requires_orchestration_charter_binding(
        subject="CHECKPOINT wave 1",
        thread_tags=[],
        supersedes_turn=1,
    )


def test_no_binding_on_steady_state_structural() -> None:
    assert not requires_orchestration_charter_binding(
        subject="CHECKPOINT wave 3",
        thread_tags=["role:root"],
        supersedes_turn=2,
    )


def test_no_binding_on_non_checkpoint() -> None:
    assert not requires_orchestration_charter_binding(
        subject="Status",
        thread_tags=["role:root"],
        supersedes_turn=None,
    )


def test_complete_birth_passes_lint() -> None:
    assert (
        lint_orchestration_charter_binding(
            _VALID_BIRTH,
            subject="CHECKPOINT — birth",
            thread_tags=[],
            supersedes_turn=None,
        )
        is None
    )


def test_missing_scoreboard_fires_advisory() -> None:
    body = """\
TYPE: CHECKPOINT

## Anchor
- Objective: Only objective here

## Next pickup
1. G1 — work
"""
    advisory = orchestration_charter_advisory(
        body=body,
        subject="CHECKPOINT — birth",
        thread_tags=[],
        supersedes_turn=None,
    )
    assert advisory is not None
    assert advisory.reason == "root_missing_charter"
    assert advisory.turn_kind == "orchestration_birth"
    assert "charter pointer" in advisory.suggestion


def test_missing_objective_fires_advisory() -> None:
    body = """\
TYPE: CHECKPOINT

## Scoreboard URI
cortex://notes/system/threads/1-charter-scoreboard.md
"""
    advisory = orchestration_charter_advisory(
        body=body,
        subject="CHECKPOINT — birth",
        thread_tags=[],
        supersedes_turn=None,
    )
    assert advisory is not None
    assert "bound objective" in advisory.suggestion


def test_continuity_doc_satisfies_manual_pointer() -> None:
    body = """\
TYPE: CHECKPOINT

Objective: Manual orchestration arc

Charter: cortex://notes/system/threads/arc-continuity-doc.md
"""
    assert (
        orchestration_charter_advisory(
            body=body,
            subject="CHECKPOINT — birth",
            thread_tags=[],
            supersedes_turn=None,
        )
        is None
    )

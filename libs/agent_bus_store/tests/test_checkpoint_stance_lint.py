"""Unit tests for continuity stance birth lint."""

from __future__ import annotations

from agent_bus_store.checkpoint_stance_lint import (
    lint_continuity_stance,
    orchestration_stance_advisory,
    requires_continuity_stance,
)

_COMPLETE = """\
TYPE: CHECKPOINT

## Stance
Use the `ulg-for-llms` skill.
Why this house: cortex://notes/system/threads/9999-continuity.md

## Anchor
- Thread: agent-bus:9999
- Objective: Ship the stance trait

Charter: cortex://notes/system/threads/9999-continuity.md
"""


def test_requires_stance_on_unenrolled_birth() -> None:
    assert requires_continuity_stance(
        subject="CHECKPOINT — birth",
        thread_tags=[],
        supersedes_turn=None,
    )


def test_enrolled_tick_skips_stance() -> None:
    assert not requires_continuity_stance(
        subject="CHECKPOINT — birth",
        thread_tags=["charter-runner", "role:root"],
        supersedes_turn=None,
    )


def test_complete_stance_passes() -> None:
    assert lint_continuity_stance(_COMPLETE) is None
    assert (
        orchestration_stance_advisory(
            body=_COMPLETE,
            subject="CHECKPOINT — birth",
            thread_tags=[],
            supersedes_turn=None,
        )
        is None
    )


def test_missing_skill_fires() -> None:
    body = """\
## Why this house
The score that remembers.

Objective: Manual arc
Charter: cortex://notes/system/threads/arc-continuity-doc.md
"""
    advisory = orchestration_stance_advisory(
        body=body,
        subject="CHECKPOINT — birth",
        thread_tags=[],
        supersedes_turn=None,
    )
    assert advisory is not None
    assert advisory.reason == "root_missing_stance"
    assert advisory.turn_kind == "continuity_stance"
    assert "ulg-for-llms" in advisory.suggestion


def test_missing_preamble_fires() -> None:
    body = """\
Use the `ulg-for-llms` skill.

Objective: Manual arc
Charter: cortex://notes/system/threads/arc-continuity-doc.md
"""
    advisory = orchestration_stance_advisory(
        body=body,
        subject="CHECKPOINT — birth",
        thread_tags=[],
        supersedes_turn=None,
    )
    assert advisory is not None
    assert "Why this house" in advisory.suggestion


def test_enrolled_missing_stance_silent() -> None:
    body = "Objective: tick\ncortex://notes/system/threads/1-charter-scoreboard.md\n"
    assert (
        orchestration_stance_advisory(
            body=body,
            subject="CHECKPOINT — birth",
            thread_tags=["charter-runner"],
            supersedes_turn=None,
        )
        is None
    )

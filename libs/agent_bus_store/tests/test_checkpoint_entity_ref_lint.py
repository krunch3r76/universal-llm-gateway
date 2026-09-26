"""Drop detector for inherited ## Entity refs on continuity checkpoints."""

from __future__ import annotations

from agent_bus_store.checkpoint_entity_ref_lint import (
    entity_ref_drop_advisory,
    entity_ref_rows,
)

_PRIOR = """\
## Entity refs
- `account:xfinity` · watch: `current_status`

## Sidecars
- cortex://notes/system/threads/example-archive.md
"""

_KEPT = """\
## Entity refs
- `account:xfinity` · watch: `current_status`
"""

_DROPPED = """\
## Stance
Use the `ulg-for-llms` skill.
"""

_NONE = """\
## Entity refs
_None yet._
"""


def test_rows_ignore_none_yet() -> None:
    assert entity_ref_rows(_PRIOR) == ["- `account:xfinity` · watch: `current_status`"]
    assert entity_ref_rows(_NONE) == []
    assert entity_ref_rows(_DROPPED) == []


def test_drop_fires() -> None:
    advisory = entity_ref_drop_advisory(
        body=_DROPPED,
        predecessor_body=_PRIOR,
        subject="CHECKPOINT 4",
        thread_tags=["role:root"],
        supersedes_turn=10,
    )
    assert advisory is not None
    assert advisory.reason == "tip_missing_entity_refs"
    assert advisory.turn_kind == "continuity_entity_refs"


def test_carry_forward_silent() -> None:
    assert (
        entity_ref_drop_advisory(
            body=_KEPT,
            predecessor_body=_PRIOR,
            subject="CHECKPOINT 4",
            thread_tags=["role:root"],
            supersedes_turn=10,
        )
        is None
    )


def test_birth_and_empty_predecessor_silent() -> None:
    assert (
        entity_ref_drop_advisory(
            body=_DROPPED,
            predecessor_body=_PRIOR,
            subject="CHECKPOINT 4",
            thread_tags=["role:root"],
            supersedes_turn=None,
        )
        is None
    )
    assert (
        entity_ref_drop_advisory(
            body=_DROPPED,
            predecessor_body=_NONE,
            subject="CHECKPOINT 4",
            thread_tags=[],
            supersedes_turn=3,
        )
        is None
    )


def test_charter_tick_silent() -> None:
    assert (
        entity_ref_drop_advisory(
            body=_DROPPED,
            predecessor_body=_PRIOR,
            subject="CHECKPOINT 4",
            thread_tags=["charter-runner"],
            supersedes_turn=10,
        )
        is None
    )

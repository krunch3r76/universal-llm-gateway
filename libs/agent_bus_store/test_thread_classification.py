"""Thin thread classification — spine + enrollment auto-stamp + role guard."""

from __future__ import annotations

import pytest

from agent_bus_store.enrollment_guard import ENROLLMENT_TAG
from agent_bus_store.thread_classification import (
    ROLE_ROOT_TAG,
    ThreadClassificationError,
    classify_thread,
    gate_thread_tags,
    resolve_spine,
)


def test_resolve_spine_default_work() -> None:
    assert resolve_spine([]) == "work"
    assert resolve_spine(["project:ulg", "type:review"]) == "work"


def test_resolve_spine_role_root() -> None:
    assert resolve_spine([ROLE_ROOT_TAG]) == "root"
    assert resolve_spine(["project:ulg", ROLE_ROOT_TAG]) == "root"


def test_resolve_spine_legacy_checkpoint_read() -> None:
    assert resolve_spine([], has_checkpoint_turn=True) == "root"
    assert (
        resolve_spine(["type:monitor"], has_checkpoint_turn=True) == "work"
    )


def test_classify_thread() -> None:
    assert classify_thread([ENROLLMENT_TAG, ROLE_ROOT_TAG]) == {
        "spine": "root",
        "enrolled": True,
    }
    assert classify_thread(["type:bug"]) == {"spine": "work", "enrolled": False}


def test_enroll_auto_stamps_role_root() -> None:
    tags = gate_thread_tags(
        [ENROLLMENT_TAG, "project:ulg"],
        prior_tags=[],
        enroll_charter_runner=True,
    )
    assert ENROLLMENT_TAG in tags
    assert ROLE_ROOT_TAG in tags
    assert resolve_spine(tags) == "root"


def test_enroll_keeps_existing_role_root() -> None:
    tags = gate_thread_tags(
        [ENROLLMENT_TAG, ROLE_ROOT_TAG],
        prior_tags=[ENROLLMENT_TAG, ROLE_ROOT_TAG],
        enroll_charter_runner=False,
    )
    assert tags.count(ROLE_ROOT_TAG) == 1


def test_unknown_role_tag_rejected() -> None:
    with pytest.raises(ThreadClassificationError) as ei:
        gate_thread_tags(
            ["role:monitor", "project:ulg"],
            prior_tags=[],
            enroll_charter_runner=False,
        )
    assert ei.value.error_code == "unknown_role_tag"


def test_role_root_alone_ok() -> None:
    tags = gate_thread_tags(
        [ROLE_ROOT_TAG, "project:ulg"],
        prior_tags=[],
        enroll_charter_runner=False,
    )
    assert tags == [ROLE_ROOT_TAG, "project:ulg"]


def test_enroll_without_flag_still_denied() -> None:
    from agent_bus_store.enrollment_guard import EnrollmentTagError

    with pytest.raises(EnrollmentTagError):
        gate_thread_tags(
            [ENROLLMENT_TAG],
            prior_tags=[],
            enroll_charter_runner=False,
        )

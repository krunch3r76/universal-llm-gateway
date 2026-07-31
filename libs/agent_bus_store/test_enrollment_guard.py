"""Enrollment dual-key gate — reserved ``charter-runner`` tag."""

from __future__ import annotations

import pytest

from agent_bus_store.enrollment_guard import (
    ENROLLMENT_TAG,
    EnrollmentTagError,
    gate_enrollment_tags,
)


def test_add_enrollment_without_flag_denied() -> None:
    with pytest.raises(EnrollmentTagError) as ei:
        gate_enrollment_tags(
            ["type:review", ENROLLMENT_TAG, "project:ulg"],
            prior_tags=[],
            enroll_charter_runner=False,
        )
    assert ei.value.error_code == "reserved_enrollment_tag"


def test_add_enrollment_with_flag_ok() -> None:
    tags = gate_enrollment_tags(
        ["project:ulg", "Charter-Runner"],
        prior_tags=[],
        enroll_charter_runner=True,
    )
    assert tags == ["project:ulg", ENROLLMENT_TAG]


def test_keep_enrollment_without_flag_ok() -> None:
    tags = gate_enrollment_tags(
        [ENROLLMENT_TAG, "type:feature"],
        prior_tags=[ENROLLMENT_TAG],
        enroll_charter_runner=False,
    )
    assert ENROLLMENT_TAG in tags


def test_remove_enrollment_without_flag_ok() -> None:
    tags = gate_enrollment_tags(
        ["type:feature"],
        prior_tags=[ENROLLMENT_TAG, "type:feature"],
        enroll_charter_runner=False,
    )
    assert ENROLLMENT_TAG not in tags

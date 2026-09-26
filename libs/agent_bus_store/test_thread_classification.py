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


def test_liaison_monitor_kind_order() -> None:
    from agent_bus_store.thread_classification import liaison_monitor_kind

    assert liaison_monitor_kind(["watches:12586", "role:root"]) == "monitor"
    assert liaison_monitor_kind(["watch:6590", "type:monitor"]) == "monitor"
    assert (
        liaison_monitor_kind(["lane:liaison", "role:root", "type:continuity"])
        == "liaison_house"
    )
    assert liaison_monitor_kind(["role:root", "type:continuity"]) == "other"
    assert liaison_monitor_kind(["lane:liaison"]) == "other"
    assert liaison_monitor_kind(["type:monitor"]) == "other"


def test_monitor_write_requires_watches_and_forbids_continuity() -> None:
    with pytest.raises(ThreadClassificationError) as missing:
        gate_thread_tags(["type:monitor"], prior_tags=[], enroll_charter_runner=False)
    assert missing.value.error_code == "monitor_watch_required"

    with pytest.raises(ThreadClassificationError) as both:
        gate_thread_tags(
            ["type:monitor", "watches:12586", "type:continuity"],
            prior_tags=[],
            enroll_charter_runner=False,
        )
    assert both.value.error_code == "monitor_continuity_conflict"

    tags = gate_thread_tags(
        ["type:monitor", "watch:12586"],
        prior_tags=[],
        enroll_charter_runner=False,
    )
    assert tags == ["type:monitor", "watches:12586"]


def test_existing_monitor_without_watches_accepts_unrelated_add() -> None:
    tags = gate_thread_tags(
        ["project:ulg"],
        prior_tags=["type:monitor", "project:ulg"],
        enroll_charter_runner=False,
        merge_prior=True,
    )
    assert tags == ["project:ulg"]


def test_child_lane_cannot_gain_liaison_tag_without_root() -> None:
    with pytest.raises(ThreadClassificationError) as ei:
        gate_thread_tags(
            ["lane:liaison"],
            prior_tags=["project:ulg"],
            enroll_charter_runner=False,
            merge_prior=True,
            child_lane=True,
        )
    assert ei.value.error_code == "liaison_tag_on_child_lane"

    kept = gate_thread_tags(
        ["lane:liaison", "role:root"],
        prior_tags=[],
        enroll_charter_runner=False,
        child_lane=True,
    )
    assert "lane:liaison" in kept
    assert "role:root" in kept

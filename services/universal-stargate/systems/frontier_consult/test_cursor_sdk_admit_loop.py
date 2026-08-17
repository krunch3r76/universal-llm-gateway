"""Unit tests for admit-pointer loop-closure classification (6655 B.1/B.2)."""

from __future__ import annotations

from .cursor_sdk_admit_loop import (
    admit_pointer_would_have_refused_total,
    classify_admit_pointer_loop,
    reset_admit_pointer_would_have_refused_counter_for_tests,
    spawn_prompt_builder_uses_latest_on_thread,
)


def setup_function() -> None:
    reset_admit_pointer_would_have_refused_counter_for_tests()


def test_row1_different_threads_not_loop() -> None:
    """Inline explicit prompt; admit to different coord thread — silent legal."""
    result = classify_admit_pointer_loop(
        admit_target_thread="1959",
        prompt_source_thread="1960",
        prompt_bind_mode="explicit_inline",
        prompt_turn_number=None,
        has_explicit_prompt_source=True,
    )
    assert result.loop_closure is False
    assert result.allowlisted_silent is True
    assert result.would_have_refused is False


def test_row2_frozen_same_thread_allowlisted() -> None:
    """Frozen turn N on same thread — observability legal, silent."""
    result = classify_admit_pointer_loop(
        admit_target_thread="1959",
        prompt_source_thread="1959",
        prompt_bind_mode="frozen_turn",
        prompt_turn_number=7,
        has_explicit_prompt_source=False,
    )
    assert result.loop_closure is False
    assert result.allowlisted_silent is True
    assert spawn_prompt_builder_uses_latest_on_thread(
        admit_target_thread="1959",
        prompt_source_thread="1959",
        prompt_bind_mode="frozen_turn",
        prompt_turn_number=7,
    ) is False


def test_row3_latest_same_thread_loop() -> None:
    """Latest resolution on T; admit to T — incident shape."""
    result = classify_admit_pointer_loop(
        admit_target_thread="7031",
        prompt_source_thread="7031",
        prompt_bind_mode="latest",
        prompt_turn_number=None,
        has_explicit_prompt_source=False,
    )
    assert result.loop_closure is True
    assert result.allowlisted_silent is False
    assert result.would_have_refused is True
    assert result.reason == "loop_closure"


def test_row4_incident_seed_explicit_same_thread_loop() -> None:
    """Review-child prompt= on T, admit to T — loop even though prompt inline."""
    result = classify_admit_pointer_loop(
        admit_target_thread="7031",
        prompt_source_thread="7031",
        prompt_bind_mode="explicit_inline",
        prompt_turn_number=None,
        has_explicit_prompt_source=True,
    )
    assert result.loop_closure is True
    assert result.allowlisted_silent is False
    assert result.would_have_refused is True
    assert result.spawn_uses_latest_on_thread is True


def test_dead_allowlist_branch_deleted_explicit_unpinned_not_legal() -> None:
    """Former 4th is_allowlisted branch must not resurrect row-4 as silent."""
    from .cursor_sdk_admit_loop import is_allowlisted_silent_legal

    assert (
        is_allowlisted_silent_legal(
            admit_target_thread="7031",
            prompt_source_thread="7031",
            prompt_bind_mode="explicit_inline",
            prompt_turn_number=None,
            has_explicit_prompt_source=True,
        )
        is False
    )


def test_loop_closure_refuse_error_names_legal_rows() -> None:
    from .cursor_sdk_admit_loop import LEGAL_ADMIT_SHAPES, loop_closure_refuse_error

    classification = classify_admit_pointer_loop(
        admit_target_thread="7031",
        prompt_source_thread="7031",
        prompt_bind_mode="latest",
        prompt_turn_number=None,
        has_explicit_prompt_source=False,
    )
    err = loop_closure_refuse_error(
        request_id="req-b3",
        classification=classification,
        admit_target_thread="7031",
        prompt_source_thread="7031",
        prompt_bind_mode="latest",
        prompt_turn_number=None,
    )
    assert err.status_code == 422
    assert err.code == "admit_pointer.loop_closure"
    assert err.details is not None
    assert err.details["retryable"] is False
    assert list(LEGAL_ADMIT_SHAPES) == err.details["data"]["legal_shapes"]


def test_b3_would_refuse_counter_increments_via_module() -> None:
    assert admit_pointer_would_have_refused_total() == 0
    from .cursor_sdk_admit_loop import increment_admit_pointer_would_have_refused

    assert increment_admit_pointer_would_have_refused() == 1
    assert admit_pointer_would_have_refused_total() == 1

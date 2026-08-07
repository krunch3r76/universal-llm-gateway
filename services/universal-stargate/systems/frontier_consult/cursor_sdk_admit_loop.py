"""Transitional loop-closure detector for post_coord_admit_pointer (6655 B.1/B.2).

Detects and counts admits that would be refused by hard-forbid (B.3) without
refusing today. Allowlist matches rank sidecar four-row table.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

_WOULD_HAVE_REFUSED_LOCK = Lock()
_WOULD_HAVE_REFUSED_TOTAL = 0


@dataclass(frozen=True, slots=True)
class AdmitLoopClassification:
    """Result of classifying one post_coord_admit_pointer call."""

    loop_closure: bool
    allowlisted_silent: bool
    would_have_refused: bool
    reason: str
    spawn_uses_latest_on_thread: bool


def spawn_prompt_builder_uses_latest_on_thread(
    *,
    admit_target_thread: str,
    prompt_source_thread: str,
    prompt_bind_mode: str | None,
    prompt_turn_number: int | None,
) -> bool:
    """True when wired spawn/prepare on this lane re-subscribes via latest on *T*."""
    if admit_target_thread != prompt_source_thread:
        return False
    if prompt_bind_mode == "frozen_turn" and prompt_turn_number is not None:
        return False
    return True


def is_allowlisted_silent_legal(
    *,
    admit_target_thread: str | None,
    prompt_source_thread: str | None,
    prompt_bind_mode: str | None,
    prompt_turn_number: int | None,
    has_explicit_prompt_source: bool,
) -> bool:
    """B.2 — legal shapes stay silent (rank four-row table rows 1–2)."""
    if not admit_target_thread or not prompt_source_thread:
        return True
    if admit_target_thread != prompt_source_thread:
        return True
    if prompt_bind_mode == "frozen_turn" and prompt_turn_number is not None:
        return True
    if has_explicit_prompt_source and not spawn_prompt_builder_uses_latest_on_thread(
        admit_target_thread=admit_target_thread,
        prompt_source_thread=prompt_source_thread,
        prompt_bind_mode=prompt_bind_mode,
        prompt_turn_number=prompt_turn_number,
    ):
        return True
    return False


def classify_admit_pointer_loop(
    *,
    admit_target_thread: str | None,
    prompt_source_thread: str | None,
    prompt_bind_mode: str | None,
    prompt_turn_number: int | None,
    has_explicit_prompt_source: bool,
) -> AdmitLoopClassification:
    """Classify loop_closure per rank predicate; never refuses."""
    if not admit_target_thread or not prompt_source_thread:
        return AdmitLoopClassification(
            loop_closure=False,
            allowlisted_silent=True,
            would_have_refused=False,
            reason="missing_thread",
            spawn_uses_latest_on_thread=False,
        )
    spawn_latest = spawn_prompt_builder_uses_latest_on_thread(
        admit_target_thread=admit_target_thread,
        prompt_source_thread=prompt_source_thread,
        prompt_bind_mode=prompt_bind_mode,
        prompt_turn_number=prompt_turn_number,
    )
    allowlisted = is_allowlisted_silent_legal(
        admit_target_thread=admit_target_thread,
        prompt_source_thread=prompt_source_thread,
        prompt_bind_mode=prompt_bind_mode,
        prompt_turn_number=prompt_turn_number,
        has_explicit_prompt_source=has_explicit_prompt_source,
    )
    same_thread = admit_target_thread == prompt_source_thread
    mode_latest = prompt_bind_mode == "latest"
    row4_explicit = has_explicit_prompt_source and prompt_turn_number is None
    loop = same_thread and not allowlisted and (
        mode_latest or spawn_latest or row4_explicit
    )
    would_refuse = loop
    if not same_thread:
        reason = "different_threads"
    elif allowlisted:
        reason = "allowlisted"
    elif loop:
        reason = "loop_closure"
    else:
        reason = "no_loop"
    return AdmitLoopClassification(
        loop_closure=loop,
        allowlisted_silent=allowlisted,
        would_have_refused=would_refuse,
        reason=reason,
        spawn_uses_latest_on_thread=spawn_latest,
    )


def admit_pointer_would_have_refused_total() -> int:
    """Readable counter for B.3 would-refuse admits (no recurrence required)."""
    with _WOULD_HAVE_REFUSED_LOCK:
        return _WOULD_HAVE_REFUSED_TOTAL


def increment_admit_pointer_would_have_refused() -> int:
    """Bump counter; returns new total."""
    global _WOULD_HAVE_REFUSED_TOTAL
    with _WOULD_HAVE_REFUSED_LOCK:
        _WOULD_HAVE_REFUSED_TOTAL += 1
        return _WOULD_HAVE_REFUSED_TOTAL


def reset_admit_pointer_would_have_refused_counter_for_tests() -> None:
    """Test isolation only."""
    global _WOULD_HAVE_REFUSED_TOTAL
    with _WOULD_HAVE_REFUSED_LOCK:
        _WOULD_HAVE_REFUSED_TOTAL = 0

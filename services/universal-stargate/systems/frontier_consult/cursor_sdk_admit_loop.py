"""Loop-closure classifier for cursor-sdk generate admission (6655 B.3).

Classify stays here; the prepare caller refuses. Allowlist matches the four-row
table: rows 1–2 legal, rows 3–4 refused. The former fourth allowlist branch
(explicit source ∧ ¬spawn_latest) is deleted — dead on same-thread because
¬spawn_latest is already row 2.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Any

from .admission import FrontierEndpointError

_WOULD_HAVE_REFUSED_LOCK = Lock()
_WOULD_HAVE_REFUSED_TOTAL = 0

LOOP_CLOSURE_CODE = "admit_pointer.loop_closure"
LEGAL_ADMIT_SHAPES = (
    "row1: prompt_source_thread != admit_target_thread",
    "row2: prompt_bind_mode=frozen_turn and prompt_turn_number is not None",
)


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
    # File / caller text is not a thread latch. Not an allowlist row.
    if prompt_bind_mode == "explicit_external":
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
    """B.2 — legal shapes stay silent (rank four-row table rows 1–2).

    ``has_explicit_prompt_source`` is retained for call-site compatibility; it
    does not admit a same-thread unpinned shape (dead branch 4 deleted).
    """
    _ = has_explicit_prompt_source
    if not admit_target_thread or not prompt_source_thread:
        return True
    if admit_target_thread != prompt_source_thread:
        return True
    if prompt_bind_mode == "frozen_turn" and prompt_turn_number is not None:
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
    """Classify loop_closure per rank predicate. Does not raise — prepare refuses."""
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
    loop = same_thread and not allowlisted and (mode_latest or spawn_latest)
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


def loop_closure_refuse_error(
    *,
    request_id: str,
    classification: AdmitLoopClassification,
    admit_target_thread: str,
    prompt_source_thread: str,
    prompt_bind_mode: str | None,
    prompt_turn_number: int | None,
) -> FrontierEndpointError:
    """422 envelope for B.3 — ProtocolError nested in ``details``, retryable=false."""
    from universal_protocol.errors import ProtocolError

    envelope: dict[str, Any] = ProtocolError(
        code=LOOP_CLOSURE_CODE,
        message=(
            "generate admission refused: unbounded same-thread prompt "
            "reference (loop_closure)"
        ),
        source="rpc",
        retryable=False,
        data={
            "legal_shapes": list(LEGAL_ADMIT_SHAPES),
            "reason": classification.reason,
            "admit_target_thread": admit_target_thread,
            "prompt_source_thread": prompt_source_thread,
            "prompt_bind_mode": prompt_bind_mode,
            "prompt_turn_number": prompt_turn_number,
        },
    ).to_dict()
    return FrontierEndpointError(
        request_id=request_id,
        field="prompt_bind_mode",
        reason=str(envelope["message"]),
        status_code=422,
        code=LOOP_CLOSURE_CODE,
        details=envelope,
    )

"""CDP page harvest and liveness observation for the dual-completion watcher."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from claude_bundles.project_ask import read_archive_execution_id

from cdp_ask.structural_quiet import (
    STRUCTURAL_QUIET_SAMPLES,
    StructuralQuietTracker,
)

# Sample-count thresholds on the held-page harvest ladder path
# (``runner.make_harvest_ladder_hook`` → ``wait_assistant_reply(poll_ms=500)`` in
# ``libs/claude_bundles/chat_reply_wait.py``). Each constant is denominated in
# consecutive harvest samples, not wall-clock seconds.

GROWTH_IDLE_SAMPLES = 2
"""Intra-arm debounce for idle-after-growth (~1s at poll_ms=500 on the verified ladder path)."""

SUSTAINED_IDLE_SAMPLES = 10
"""Continuous quiet after ``seen_active`` (≥5s at poll_ms=500 via ``chat_reply_wait.py``)."""

ESCAPE_IDLE_SAMPLES = 120
"""Failsafe when generation never flips ``seen_active`` (~60s at poll_ms=500)."""

TurnIdleArm = Literal["growth", "sustained", "escape", "structural_quiet"]


@dataclass
class LadderCallbacks:
    """Optional hooks for dual-completion ladder updates during ``run_execution``.

    ``on_liveness`` receives ``(streaming, stop, tool_pause, observed_at)`` after each
    successful ``harvest_assistant`` sample while the watcher runs. Advisory only — must
    not gate ``on_turn_idle``, ``on_content_proof``, or stall classification.
    """

    on_turn_idle: Callable[[], Awaitable[None]] | None = None
    on_content_proof: Callable[[str, str], Awaitable[None]] | None = None
    on_archiving: Callable[[], Awaitable[None]] | None = None
    on_liveness: Callable[[bool, bool, bool, float], Awaitable[None]] | None = None
    abort_check: Callable[[], Awaitable[bool]] | None = None


@dataclass
class LadderAdvanceState:
    """Mutable progress for one execution's dual-completion ladder.

    ``execution_id``: when non-empty, ``content_proof`` requires each watched
    archive file to carry a matching stamped ``execution_id``. Empty
    ``execution_id`` against an occupied file fails closed (no size-only proof).

    ``seen_active``: sticky one-way latch; set on the first sample with
    streaming, stop, or tool_pause and never reset for this progress instance.

    ``idle_streak``: count of consecutive idle samples; reset to 0 on any
    non-idle sample.

    ``body_len_baseline``: ``body_len`` captured on the same sample that first
    flips ``seen_active``; remains ``None`` until then.
    """

    turn_idle_sent: bool = False
    content_proof_sent: bool = False
    targets: list[tuple[Path, str]] = field(default_factory=list)
    min_bytes: int = 1
    sha256_file: Callable[[Path], str] | None = None
    execution_id: str = ""
    output_download_pending: bool = False
    blocked_archive_paths: set[Path] = field(default_factory=set)
    seen_active: bool = False
    idle_streak: int = 0
    body_len_baseline: int | None = None
    turn_idle_arm: TurnIdleArm | None = None
    structural_quiet: StructuralQuietTracker = field(
        default_factory=StructuralQuietTracker
    )


def archive_stamp_allows_content_proof(
    archive_path: Path,
    execution_id: str,
) -> bool:
    """Return whether *archive_path* may satisfy ``content_proof`` for *execution_id*.

    Non-empty *execution_id* activates identity-gated proof: the file must carry
    a stamped ``execution_id`` line matching the current run. Missing stamp,
    foreign stamp, or empty *execution_id* against an occupied file all fail
    closed — no size-only fallback.
    """
    if not execution_id:
        return False
    stamp = read_archive_execution_id(str(archive_path))
    return stamp is not None and stamp == execution_id


def page_idle_from_state(state: dict[str, Any]) -> bool:
    """Derive turn-idle from a harvest triple — any active signal means not idle."""
    return not (
        state.get("streaming")
        or state.get("stop")
        or state.get("tool_pause")
    )


async def advance_ladder_from_harvest(
    state: dict[str, Any],
    *,
    callbacks: LadderCallbacks,
    progress: LadderAdvanceState,
) -> None:
    """Advance dual-completion rungs from one held-page ``harvest_assistant`` sample.

    Callers must feed samples from the Playwright page already held by
    ``wait_assistant_reply`` / ask / converse — never open a competing CDP
    connection (friction 25671).

    Turn-idle latching is gated: ``on_turn_idle`` fires only after sustained idle
    after activity, idle-after-growth (body delta past ``min_bytes`` with debounce),
    or the ``ESCAPE_IDLE_SAMPLES`` failsafe when generation never activates. Idle is
    defined as ``¬(streaming ∨ stop ∨ tool_pause)`` via ``page_idle_from_state``, so
    a sample that sets ``seen_active`` cannot co-occur with ``idle=True`` on that
    same sample.
    """
    if callbacks.abort_check and await callbacks.abort_check():
        return
    if callbacks.on_liveness:
        await callbacks.on_liveness(
            bool(state.get("streaming")),
            bool(state.get("stop")),
            bool(state.get("tool_pause")),
            time.time(),
        )

    streaming = bool(state.get("streaming"))
    stop = bool(state.get("stop"))
    tool_pause = bool(state.get("tool_pause"))
    if streaming or stop or tool_pause:
        if not progress.seen_active:
            progress.seen_active = True
            progress.body_len_baseline = int(state.get("body_len") or 0)
        else:
            progress.seen_active = True

    progress.structural_quiet.observe(state)

    idle = page_idle_from_state(state)
    if not idle:
        progress.idle_streak = 0
    else:
        progress.idle_streak += 1

    body_len = int(state.get("body_len") or 0)
    baseline = progress.body_len_baseline
    idle_after_growth = (
        progress.seen_active
        and baseline is not None
        and (body_len - baseline) >= progress.min_bytes
        and progress.idle_streak >= GROWTH_IDLE_SAMPLES
    )
    sustained_after_active = (
        progress.seen_active and progress.idle_streak >= SUSTAINED_IDLE_SAMPLES
    )
    escape_idle = (
        not progress.seen_active and progress.idle_streak >= ESCAPE_IDLE_SAMPLES
    )
    structural_quiet_arm = (
        progress.structural_quiet.quiet_satisfied
        and (streaming or stop or tool_pause)
        and progress.seen_active
        and baseline is not None
        and (body_len - baseline) >= progress.min_bytes
    )
    should_fire_turn_idle = (
        idle_after_growth
        or sustained_after_active
        or escape_idle
        or structural_quiet_arm
    )

    if (
        (idle or structural_quiet_arm)
        and not progress.turn_idle_sent
        and callbacks.on_turn_idle
        and should_fire_turn_idle
    ):
        if structural_quiet_arm:
            progress.turn_idle_arm = "structural_quiet"
        elif idle_after_growth:
            progress.turn_idle_arm = "growth"
        elif sustained_after_active:
            progress.turn_idle_arm = "sustained"
        else:
            progress.turn_idle_arm = "escape"
        progress.turn_idle_sent = True
        await callbacks.on_turn_idle()

    if (
        idle
        and progress.turn_idle_sent
        and not progress.content_proof_sent
        and progress.sha256_file is not None
    ):
        for path, uri in progress.targets:
            try:
                if not path.is_file() or path.stat().st_size < progress.min_bytes:
                    continue
            except OSError:
                continue
            if (
                progress.output_download_pending
                and path.resolve() in progress.blocked_archive_paths
            ):
                continue
            if not archive_stamp_allows_content_proof(path, progress.execution_id):
                continue
            if callbacks.on_content_proof:
                progress.content_proof_sent = True
                await callbacks.on_content_proof(uri, progress.sha256_file(path))
            break


def make_harvest_ladder_hook(
    *,
    callbacks: LadderCallbacks,
    progress: LadderAdvanceState,
) -> Callable[[dict[str, Any]], Awaitable[None]]:
    """Return an ``on_harvest`` callback bound to *callbacks* / *progress*."""

    async def _on_harvest(state: dict[str, Any]) -> None:
        await advance_ladder_from_harvest(
            state, callbacks=callbacks, progress=progress
        )

    return _on_harvest

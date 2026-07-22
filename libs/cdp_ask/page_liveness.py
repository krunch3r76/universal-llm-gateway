"""CDP page harvest and liveness observation for the dual-completion watcher."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claude_bundles.project_ask import read_archive_execution_id


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
    """

    turn_idle_sent: bool = False
    content_proof_sent: bool = False
    targets: list[tuple[Path, str]] = field(default_factory=list)
    min_bytes: int = 1
    sha256_file: Callable[[Path], str] | None = None
    execution_id: str = ""


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
    idle = page_idle_from_state(state)
    if idle and not progress.turn_idle_sent and callbacks.on_turn_idle:
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

"""Scoreboard locus — identity and location are not fused.

Work-item tips live under ``notes/system/scoreboards/{slug}-scoreboard.md``.
Charter tips live under ``notes/system/threads/{thread}-charter-scoreboard.md``.
The two populations are different document genres; a locus carries paths and
an optional rewind guard so the journal kernel does not assume a G-ladder.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from implement_admission.closeout_helpers import cortex_files_root

_SCOREBOARDS_DIR = "notes/system/scoreboards"
_THREADS_DIR = "notes/system/threads"

RewindGuard = Callable[..., str | None]


@dataclass(frozen=True, slots=True)
class ScoreboardLocus:
    """Resolved tip + journal paths for one scoreboard, with an optional rewind guard."""

    tip_path: Path
    journal_path: Path
    tip_uri: str
    journal_uri: str
    rewind_guard: RewindGuard | None = None


def work_item_locus(
    slug: str,
    *,
    files_root: Path | None = None,
    rewind_guard: RewindGuard | None = None,
) -> ScoreboardLocus:
    """Locus for a conductor work-item scoreboard keyed by todo slug."""
    root = files_root if files_root is not None else cortex_files_root()
    return ScoreboardLocus(
        tip_path=root / _SCOREBOARDS_DIR / f"{slug}-scoreboard.md",
        journal_path=root / _SCOREBOARDS_DIR / f"{slug}-score-journal.md",
        tip_uri=f"cortex://{_SCOREBOARDS_DIR}/{slug}-scoreboard.md",
        journal_uri=f"cortex://{_SCOREBOARDS_DIR}/{slug}-score-journal.md",
        rewind_guard=rewind_guard,
    )


def charter_locus(
    thread: str,
    *,
    files_root: Path | None = None,
    rewind_guard: RewindGuard | None = None,
) -> ScoreboardLocus:
    """Locus for a continuity-root charter scoreboard keyed by thread id."""
    root = files_root if files_root is not None else cortex_files_root()
    return ScoreboardLocus(
        tip_path=root / _THREADS_DIR / f"{thread}-charter-scoreboard.md",
        journal_path=root / _THREADS_DIR / f"{thread}-charter-score-journal.md",
        tip_uri=f"cortex://{_THREADS_DIR}/{thread}-charter-scoreboard.md",
        journal_uri=f"cortex://{_THREADS_DIR}/{thread}-charter-score-journal.md",
        rewind_guard=rewind_guard,
    )

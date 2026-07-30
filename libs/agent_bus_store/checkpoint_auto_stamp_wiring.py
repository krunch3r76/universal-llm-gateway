"""Auto-stamp ``role:root`` on bootstrap structural CHECKPOINT when flag is on."""

from __future__ import annotations

from .checkpoint_kind_detector import (
    checkpoint_auto_stamp_enabled,
    is_bootstrap_structural_checkpoint,
)
from .db.threads import add_tags, get_thread
from .events.checkpoint_auto_stamp import emit_checkpoint_auto_stamp
from .thread_classification import ROLE_ROOT_TAG


def maybe_auto_stamp_root_on_checkpoint(
    *,
    thread: str,
    subject: str,
    thread_tags: list[str],
    supersedes_turn: int | None,
    turn_number: int,
) -> list[str]:
    """Stamp ``role:root`` on bootstrap structural CHECKPOINT when env flag is on."""
    if not checkpoint_auto_stamp_enabled():
        return thread_tags
    if not is_bootstrap_structural_checkpoint(
        subject=subject,
        thread_tags=thread_tags,
        supersedes_turn=supersedes_turn,
    ):
        return thread_tags
    if ROLE_ROOT_TAG in {t.lower() for t in thread_tags}:
        return thread_tags
    updated = add_tags(thread, [ROLE_ROOT_TAG])
    if updated is not None:
        emit_checkpoint_auto_stamp(
            thread=thread,
            turn_number=turn_number,
            subject=subject,
        )
        return list(updated.get("tags") or [])
    return thread_tags


def load_thread_tags(thread: str) -> list[str]:
    """Return current thread tags or [] when thread is missing."""
    row = get_thread(thread)
    if row is None:
        return []
    return list(row.get("tags") or [])


__all__ = [
    "load_thread_tags",
    "maybe_auto_stamp_root_on_checkpoint",
]

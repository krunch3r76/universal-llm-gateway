"""Test-only fault injection hooks at transaction boundaries."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

Hook = Callable[[], None]

_after_journal_insert: Hook | None = None
_after_head_update: Hook | None = None
_after_cursor_update: Hook | None = None
_during_checkpoint_write: Hook | None = None


def set_after_journal_insert(hook: Hook | None) -> None:
    global _after_journal_insert
    _after_journal_insert = hook


def set_after_head_update(hook: Hook | None) -> None:
    global _after_head_update
    _after_head_update = hook


def set_after_cursor_update(hook: Hook | None) -> None:
    global _after_cursor_update
    _after_cursor_update = hook


def set_during_checkpoint_write(hook: Hook | None) -> None:
    global _during_checkpoint_write
    _during_checkpoint_write = hook


def clear_all() -> None:
    set_after_journal_insert(None)
    set_after_head_update(None)
    set_after_cursor_update(None)
    set_during_checkpoint_write(None)


def maybe_after_journal_insert() -> None:
    if _after_journal_insert is not None:
        _after_journal_insert()


def maybe_after_head_update() -> None:
    if _after_head_update is not None:
        _after_head_update()


def maybe_after_cursor_update() -> None:
    if _after_cursor_update is not None:
        _after_cursor_update()


def maybe_during_checkpoint_write(state: dict[str, Any]) -> None:
    del state
    if _during_checkpoint_write is not None:
        _during_checkpoint_write()

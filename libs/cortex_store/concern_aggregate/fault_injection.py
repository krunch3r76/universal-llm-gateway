"""Test-only fault injection hooks at transaction boundaries."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

Hook = Callable[[], None]

_after_journal_insert: Hook | None = None
_after_write_intent_insert: Hook | None = None
_after_head_update: Hook | None = None
_after_cursor_update: Hook | None = None
_during_checkpoint_write: Hook | None = None

_enabled: bool | None = None


class FaultInjectionDisabledError(RuntimeError):
    """Raised when registering hooks while fault injection is disabled."""


def is_enabled() -> bool:
    global _enabled
    if _enabled is None:
        _enabled = os.environ.get("CORTEX_CONCERN_FAULT_INJECTION", "").strip() in {
            "1",
            "true",
            "yes",
        }
    return _enabled


def _require_enabled_for_set() -> None:
    if not is_enabled():
        raise FaultInjectionDisabledError(
            "fault injection disabled; set CORTEX_CONCERN_FAULT_INJECTION=1 in tests"
        )


def set_after_journal_insert(hook: Hook | None) -> None:
    global _after_journal_insert
    if hook is not None:
        _require_enabled_for_set()
    _after_journal_insert = hook


def set_after_write_intent_insert(hook: Hook | None) -> None:
    global _after_write_intent_insert
    if hook is not None:
        _require_enabled_for_set()
    _after_write_intent_insert = hook


def set_after_head_update(hook: Hook | None) -> None:
    global _after_head_update
    if hook is not None:
        _require_enabled_for_set()
    _after_head_update = hook


def set_after_cursor_update(hook: Hook | None) -> None:
    global _after_cursor_update
    if hook is not None:
        _require_enabled_for_set()
    _after_cursor_update = hook


def set_during_checkpoint_write(hook: Hook | None) -> None:
    global _during_checkpoint_write
    if hook is not None:
        _require_enabled_for_set()
    _during_checkpoint_write = hook


def clear_all() -> None:
    global _after_journal_insert, _after_write_intent_insert, _after_head_update
    global _after_cursor_update, _during_checkpoint_write
    _after_journal_insert = None
    _after_write_intent_insert = None
    _after_head_update = None
    _after_cursor_update = None
    _during_checkpoint_write = None


def maybe_after_journal_insert() -> None:
    if is_enabled() and _after_journal_insert is not None:
        _after_journal_insert()


def maybe_after_write_intent_insert() -> None:
    if is_enabled() and _after_write_intent_insert is not None:
        _after_write_intent_insert()


def maybe_after_head_update() -> None:
    if is_enabled() and _after_head_update is not None:
        _after_head_update()


def maybe_after_cursor_update() -> None:
    if is_enabled() and _after_cursor_update is not None:
        _after_cursor_update()


def maybe_during_checkpoint_write(state: dict[str, Any]) -> None:
    del state
    if is_enabled() and _during_checkpoint_write is not None:
        _during_checkpoint_write()

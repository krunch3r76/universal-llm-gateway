"""Dispatch-scoped context for agent-seat tool execution."""

from __future__ import annotations

from contextvars import ContextVar, Token

_active_persona: ContextVar[str | None] = ContextVar("active_persona", default=None)


def bind_active_persona(persona: str | None) -> Token | None:
    """Bind the dispatched persona for nested tool calls. Returns reset token."""
    if not persona:
        return None
    return _active_persona.set(persona)


def reset_active_persona(token: Token | None) -> None:
    if token is not None:
        _active_persona.reset(token)


def get_active_persona() -> str | None:
    return _active_persona.get()

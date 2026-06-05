"""Dispatch-scoped context for agent-seat tool execution."""

from __future__ import annotations

from contextvars import ContextVar, Token

_active_role: ContextVar[str | None] = ContextVar("active_role", default=None)


def bind_active_role(role: str | None) -> Token | None:
    """Bind the dispatched role for nested tool calls. Returns reset token."""
    if not role:
        return None
    return _active_role.set(role)


def reset_active_role(token: Token | None) -> None:
    if token is not None:
        _active_role.reset(token)


def get_active_role() -> str | None:
    return _active_role.get()

"""Claude Code (claude.ai/code) dispatch toggle — route_policy.yaml authority."""

from __future__ import annotations

from typing import Final, Literal

from implement_admission.routing import load_route_policy

ClaudeCodeDispatchMode = Literal["prohibit", "allow"]

_VALID_MODES: Final[frozenset[str]] = frozenset({"prohibit", "allow"})
_DEFAULT_MODE: ClaudeCodeDispatchMode = "prohibit"
_TOGGLE_KEY = "claude_code_dispatch"
_RULING_URI = "cortex://notes/rulings/skills-delivery-2026-11-10.md"


def load_claude_code_dispatch_mode(path=None) -> ClaudeCodeDispatchMode:
    """Read ``claude_code_dispatch`` from route policy (default ``prohibit``)."""
    policy = load_route_policy(path)
    raw = str(policy.get(_TOGGLE_KEY) or _DEFAULT_MODE).strip().lower()
    if raw not in _VALID_MODES:
        msg = (
            f"{_TOGGLE_KEY} must be one of {sorted(_VALID_MODES)!r}, got {raw!r}"
        )
        raise ValueError(msg)
    return raw  # type: ignore[return-value]


def is_claude_code_surface(claude_surface: str | None) -> bool:
    """True when the admit body targets the claude.ai/code compose surface."""
    return (claude_surface or "").strip().lower() == "code"


def claude_code_dispatch_prohibited(path=None) -> bool:
    return load_claude_code_dispatch_mode(path) == "prohibit"


def claude_code_dispatch_ruling_uri() -> str:
    return _RULING_URI


def claude_code_dispatch_toggle_key() -> str:
    return _TOGGLE_KEY


__all__ = [
    "ClaudeCodeDispatchMode",
    "claude_code_dispatch_prohibited",
    "claude_code_dispatch_ruling_uri",
    "claude_code_dispatch_toggle_key",
    "is_claude_code_surface",
    "load_claude_code_dispatch_mode",
]

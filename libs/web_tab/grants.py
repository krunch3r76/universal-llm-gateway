"""Grant gate for mutating web-tab actuation (aligns with paste_as_operator / chat_session)."""

from __future__ import annotations

from typing import Literal

GrantKind = Literal["explicit", "operator"]

_ALLOWED: frozenset[str] = frozenset({"explicit", "operator"})


def require_grant(grant: str | None) -> dict[str, object] | None:
    """Return refusal dict when *grant* is missing or invalid; else ``None``."""
    g = (grant or "").strip()
    if g not in _ALLOWED:
        return {
            "ok": False,
            "code": "grant_required",
            "reason": "grant must be explicit or operator",
        }
    return None

"""Recipient-scoped unread labeling for liaison digest lane/root rows."""

from __future__ import annotations

from typing import Any

DIGEST_UNREAD_TO = "cursor"


def lane_unread_fields(thread: dict[str, Any]) -> dict[str, Any]:
    """Map ThreadDetail unread_count onto ``unread`` or ``unstamped``."""
    count = thread.get("unread_count")
    basis = thread.get("unread_basis")
    recipient = basis.get("recipient") if isinstance(basis, dict) else None
    if recipient:
        return {"unread": count}
    return {"unstamped": count}


def root_unread_key(root: dict[str, Any]) -> str:
    basis = root.get("unread_basis")
    if isinstance(basis, dict) and basis.get("recipient"):
        return "unread"
    return "unstamped"

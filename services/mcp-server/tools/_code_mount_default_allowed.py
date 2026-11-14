"""Whether blank seat/caller may use the code-mount default (cursor)."""

from __future__ import annotations

from typing import Any


def code_mount_default_allowed(meta: dict[str, Any]) -> bool:
    """True when /mcp/code may infer cursor without an explicit seat/caller.

    Static or missing ``auth_mode`` keeps the local Cursor default; other modes
    (e.g. OAuth) must pass seat/caller explicitly.
    """
    auth_mode = meta.get("auth_mode")
    if auth_mode in (None, "", "static"):
        return True
    return False

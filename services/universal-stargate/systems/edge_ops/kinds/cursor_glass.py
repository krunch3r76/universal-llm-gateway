"""Admit-time validation for ``kind=cursor_glass`` (defense in depth with MCP)."""

from __future__ import annotations

from typing import Any

from ..model import CODE_GRANT_REFUSED, EdgeOpRefusedError

_ALLOWED_GRANTS = frozenset({"bridge", "explicit"})
_WAKE_RENAME_OPS = frozenset({"wake_tab", "rename_tab"})


def validate_cursor_glass_admit(*, op: str, args: dict[str, Any]) -> None:
    """Refuse admits that fail grant checks or name an unsupported op.

    Raises:
        EdgeOpRefusedError: 400 when grant, authorization_source, or op fail.
    """
    if op not in _WAKE_RENAME_OPS:
        raise EdgeOpRefusedError(
            400,
            CODE_GRANT_REFUSED,
            f"cursor_glass op {op!r} is not admitted on this slice "
            f"(allowed: {sorted(_WAKE_RENAME_OPS)}).",
        )
    grant = str(args.get("grant") or "").strip().lower()
    auth = str(args.get("authorization_source") or "").strip()
    if grant not in _ALLOWED_GRANTS or not auth:
        raise EdgeOpRefusedError(
            400,
            CODE_GRANT_REFUSED,
            "cursor_glass requires grant (bridge|explicit) and "
            "authorization_source in args.",
        )


__all__ = ["validate_cursor_glass_admit"]

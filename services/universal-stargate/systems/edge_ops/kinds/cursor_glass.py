"""Admit-time validation for ``kind=cursor_glass`` (defense in depth with MCP)."""

from __future__ import annotations

from typing import Any

from .. import config
from ..model import (
    CODE_ARGS_INVALID,
    CODE_KIND_DISABLED,
    CODE_OP_UNSUPPORTED,
    EdgeOpRefusedError,
)

_WAKE_RENAME_OPS = frozenset({"wake_tab", "rename_tab"})


def validate_cursor_glass_admit(*, op: str, args: dict[str, Any]) -> None:
    """Refuse admits when the slice gate is off, op is unknown, or args fail.

    Raises:
        EdgeOpRefusedError: 503/400 with typed refusal codes.
    """
    if not config.cursor_glass_admit_enabled():
        raise EdgeOpRefusedError(
            503,
            CODE_KIND_DISABLED,
            "cursor_glass edge ops are disabled on this slice "
            f"(set {config.CURSOR_GLASS_ENV_KEY}=true to admit).",
        )
    if op not in _WAKE_RENAME_OPS:
        raise EdgeOpRefusedError(
            400,
            CODE_OP_UNSUPPORTED,
            f"cursor_glass op {op!r} is not admitted on this slice "
            f"(allowed: {sorted(_WAKE_RENAME_OPS)}).",
        )
    if op == "wake_tab":
        if not str(args.get("gui_host") or "").strip():
            raise EdgeOpRefusedError(
                400,
                CODE_ARGS_INVALID,
                "wake_tab requires gui_host in args.",
            )
        if not str(args.get("body") or "").strip():
            raise EdgeOpRefusedError(
                400,
                CODE_ARGS_INVALID,
                "wake_tab requires body in args.",
            )
    if op == "rename_tab":
        if not str(args.get("gui_host") or "").strip():
            raise EdgeOpRefusedError(
                400,
                CODE_ARGS_INVALID,
                "rename_tab requires gui_host in args.",
            )
        if not str(args.get("agent_id") or "").strip():
            raise EdgeOpRefusedError(
                400,
                CODE_ARGS_INVALID,
                "rename_tab requires agent_id in args.",
            )
        if not str(args.get("title") or "").strip():
            raise EdgeOpRefusedError(
                400,
                CODE_ARGS_INVALID,
                "rename_tab requires title in args.",
            )


__all__ = ["validate_cursor_glass_admit"]

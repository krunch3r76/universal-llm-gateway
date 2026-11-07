"""Cursor endpoint subscribe display resolution and override handling."""

from __future__ import annotations

import re
from typing import Any

from bus_watch.cursor_tab_whereami import resolve_cursor_tab_whereami
from cursor_bridge.wake_delivery import normalize_target

from .subscribe_errors import SubscribeError

_AGENT_ID_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I,
)

_MISMATCH_BASES = frozenset({"vscdb", "env"})


def normalized_subscription_display(display: str, *, surface: str = "cursor") -> str:
    """Map a caller display token to the journal-normalized display label."""
    target = normalize_target(display, surface)
    if not target.get("ok"):
        raise SubscribeError("endpoint_target_refused")
    return str(target["display"])


def resolve_cursor_display_for_subscribe(
    *,
    display: str | None,
    agent_id: str | None,
    thread_scope: str,
    display_override: bool = False,
    display_override_reason: str | None = None,
) -> tuple[str, dict[str, Any] | None, dict[str, Any]]:
    """Return display label, optional override event payload, and placement metadata."""
    aid = str(agent_id or "").strip()
    supplied_raw = str(display or "").strip() or None
    placement = resolve_cursor_tab_whereami(transcript_id=aid, root_id=thread_scope)
    resolved_raw = str(placement.get("subscription_display") or "").strip() or None
    basis = str(placement.get("basis") or "").strip()
    placement_meta = {
        "placement_basis": basis,
        "inference_reason": placement.get("inference_reason"),
    }

    if display_override:
        reason = str(display_override_reason or "").strip()
        if not reason:
            raise SubscribeError("display_override_reason_required")
        if not supplied_raw:
            raise SubscribeError("endpoint_surface_display_required")
        normalized = normalized_subscription_display(supplied_raw)
        event = {
            "agent_id": aid,
            "thread_scope": thread_scope,
            "supplied_display": normalized,
            "resolved_display": resolved_raw,
            "basis": basis,
            "reason": reason,
        }
        return normalized, event, placement_meta

    if not supplied_raw:
        if not resolved_raw:
            raise SubscribeError("endpoint_display_unresolved")
        return resolved_raw, None, placement_meta

    supplied_norm = normalized_subscription_display(supplied_raw)
    if (
        aid
        and _AGENT_ID_UUID_RE.match(aid)
        and basis in _MISMATCH_BASES
        and resolved_raw
    ):
        resolved_norm = normalized_subscription_display(resolved_raw)
        if supplied_norm != resolved_norm:
            raise SubscribeError(
                "display_mismatch",
                resolved_display=resolved_norm,
                supplied_display=supplied_norm,
            )
    return supplied_norm, None, placement_meta


__all__ = [
    "normalized_subscription_display",
    "resolve_cursor_display_for_subscribe",
]

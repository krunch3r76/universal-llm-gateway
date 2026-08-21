"""Advisory ``park_hint`` nested under ``agent_bus.request`` poll_hint (B3).

Cowork / life web seats may park after a bounded poll window instead of
re-arming empty ≤60s holds for nests that outlast one continuous hold.
No server behaviour keys off ``park_hint`` — seat protocol only.
"""

from __future__ import annotations

from typing import Any

from claude_bundles.operator_mailbox import is_operator_proxy_mailbox

# Align with poll_hint.max_expected_latency_s and Cowork continuous hold (60s).
PARK_AFTER_S = 60
_MAX_EXPECTED_LATENCY_S = 60
_SUGGESTED_INTERVAL_S = 2

_DEFAULT_PARK_HINT: dict[str, Any] = {
    "park_after_s": PARK_AFTER_S,
    "wake": "chat_delivery",
    "fallback": "bus_wake+pager",
    "record": "PARKED",
}


def is_chat_delivery_capable(from_agent: str) -> bool:
    """True for Cowork / operator-proxy callers; false for IDE ``cursor``."""
    return is_operator_proxy_mailbox(from_agent)


def default_park_hint() -> dict[str, Any]:
    """Return a copy of the advisory park_hint object for poll_hint nesting."""
    return dict(_DEFAULT_PARK_HINT)


def build_poll_hint(
    *,
    thread_id: str,
    after_turn: int,
    from_agent: str = "",
) -> dict[str, Any]:
    """Compose the ``poll_hint`` returned by ``agent_bus.request``."""
    hint: dict[str, Any] = {
        "tool": "wait",
        "arguments_json": {
            "thread": str(thread_id),
            "after_turn": after_turn,
            "completion": "status:done",
            "wait_seconds": 0,
        },
        "suggested_interval_s": _SUGGESTED_INTERVAL_S,
        "max_expected_latency_s": _MAX_EXPECTED_LATENCY_S,
        "alternate_completions": [
            "status:failed",
            "status:needs-attended",
            "status:blocked",
            "status:superseded",
        ],
    }
    if is_chat_delivery_capable(from_agent):
        hint["park_hint"] = default_park_hint()
    return hint


__all__ = [
    "PARK_AFTER_S",
    "build_poll_hint",
    "default_park_hint",
    "is_chat_delivery_capable",
]

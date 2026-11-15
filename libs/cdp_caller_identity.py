"""CDP ``caller_agent`` deliverability — shared by MCP hop and Stargate generate admit.

MCP runs with ``PYTHONPATH`` = ``libs`` + ``services/mcp-server`` only; this module
must not import ``systems.*``.
"""

from __future__ import annotations

from functools import lru_cache

from agent_seat.registry import normalize_bus_address
from bus_watch.endpoint_wake_dispatcher import (
    GATE_WAKE_REMAP_ADDRESSES,
    WAKE_BROADCAST_DROP_ADDRESSES,
)
from claude_bundles.cdp_model_endpoint import CDP_REPLY_FROM

_CDP_SELF_REPLY_ALIASES = frozenset(
    {
        CDP_REPLY_FROM,
        "cdp",
        "web",
        "web_claude",
        "claude-web",
    }
)


@lru_cache(maxsize=1)
def _non_deliverable_caller_addresses() -> frozenset[str]:
    """Addresses the wake layer remaps, drops, or cannot deliver to."""
    folded: set[str] = set()
    for addr in (
        *GATE_WAKE_REMAP_ADDRESSES,
        *_CDP_SELF_REPLY_ALIASES,
        *WAKE_BROADCAST_DROP_ADDRESSES,
        "unspecified",
        "dispatch",
    ):
        folded.add(str(addr).casefold())
        folded.add(normalize_bus_address(str(addr)).casefold())
    return frozenset(folded)


def normalize_cdp_caller_agent(raw: object) -> str:
    """Return stripped caller_agent string (may be empty)."""
    return str(raw or "").strip()


def is_valid_cdp_caller_agent(raw: object) -> bool:
    """True when ``raw`` is a non-empty bus mailbox deliverable at wake."""
    val = normalize_cdp_caller_agent(raw)
    if not val:
        return False
    candidates = {val.casefold(), normalize_bus_address(val).casefold()}
    return not any(c in _non_deliverable_caller_addresses() for c in candidates)


def resolve_cdp_reply_to_agent(caller_agent: str | None) -> str | None:
    """Normalize ``caller_agent`` for on-behalf delivery; None when unusable."""
    if not is_valid_cdp_caller_agent(caller_agent):
        return None
    return normalize_cdp_caller_agent(caller_agent)
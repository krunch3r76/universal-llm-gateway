"""Operator-proxy mailbox predicates shared by hop-watch, GIW paste, and MCP park_hint.

Centralizes the web-* and cdp-operator-* capability test so GIW wake delivery
and MCP park_hint stay aligned with hop-cadence enroll rules.
"""

from __future__ import annotations

from agent_seat.registry import normalize_bus_address

__all__ = ["is_operator_proxy_mailbox"]


def is_operator_proxy_mailbox(from_agent: str) -> bool:
    """True when *from_agent* owns a Cowork operator CSE seat (web-* or cdp-operator-*)."""
    addr = normalize_bus_address((from_agent or "").strip())
    if not addr or addr == "cursor":
        return False
    return addr.startswith("web-") or addr.startswith("cdp-operator-")

"""Life-surface op allowlist for the email dispatch tool.

On /mcp/life the operator seat needs mailbox verification (delivery, bounces,
inbound replies) and outbound send (draft-first, tier D/O) without ingest or
IMAP mutation paths available on code.
"""

from __future__ import annotations

from typing import Any

from request_profile import current_request_metadata
from tools.local._email_catalog import CATALOG

_LIFE_ALLOWED_TIERS = frozenset({"R", "D", "O"})


def current_mcp_surface(default: str = "code") -> str:
    """Return the active MCP mount surface from request metadata."""
    surface = current_request_metadata().get("surface")
    return str(surface) if surface else default


def email_op_allowed_on_surface(op: str, *, surface: str | None = None) -> bool:
    """True when *op* may run on *surface* (life: tier R/D/O only)."""
    active = surface or current_mcp_surface()
    if active != "life":
        return True
    tier = str(CATALOG.get(op, {}).get("tier", ""))
    return tier in _LIFE_ALLOWED_TIERS


def life_surface_op_denial(op: str) -> dict[str, Any]:
    """Structured 422-style denial for a disallowed op on /mcp/life."""
    meta = CATALOG.get(op, {})
    tier = meta.get("tier", "?")
    return {
        "error": "life_surface_read_only",
        "surface": "life",
        "op": op,
        "tier": tier,
        "message": (
            f"Email op {op!r} (tier {tier}) is not available on /mcp/life. "
            "Life exposes tier-R read ops plus tier-D/O draft and send "
            "(list, status, get, recent, search, draft_new, send, …). "
            "Use /mcp/code for ingest, move, pull, and other mutation ops."
        ),
    }

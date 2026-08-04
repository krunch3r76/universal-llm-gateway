"""M365 mailbox resolution for email dispatch routing."""

from __future__ import annotations

from typing import Any

from email_routing.surface_guard import _normalize_addr, load_m365_upns


def resolve_m365_account(
    account: str | None = None,
    *,
    mailbox: str | None = None,
) -> str | None:
    """Return M365 UPN when routing should use Graph; None for IMAP bridge."""
    upns = load_m365_upns()
    if not upns:
        return None
    explicit = account or mailbox
    if explicit:
        norm = _normalize_addr(str(explicit))
        return norm if norm in upns else None
    if len(upns) == 1:
        return next(iter(upns))
    return None


def m365_account_required_error() -> dict[str, Any]:
    """Structured error when multiple M365 mailboxes need disambiguation."""
    upns = sorted(load_m365_upns())
    return {
        "error": "account_required",
        "transport": "m365_graph",
        "m365_accounts": upns,
        "message": (
            "Multiple M365 mailboxes configured; pass account or mailbox "
            f"UPN. Known: {', '.join(upns)}"
        ),
    }


def graph_client_unavailable_error(exc: Exception) -> dict[str, Any]:
    return {
        "error": "graph_unavailable",
        "transport": "m365_graph",
        "message": str(exc),
    }

"""MCP-side pickup_awaits gate helpers (load prior turns + refuse)."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from mcp_events import record

from ._shared import relay


def load_prior_turns(thread: str | None) -> list[dict[str, Any]] | None:
    """Fetch recent turns for cease-to-act unbound-pickup scan.

    Returns ``None`` when thread is absent or fetch fails (declaration gate
    still runs; stop-time history scan is skipped rather than guessed).
    """
    if not thread:
        return None
    qs = urlencode({"thread": str(thread), "last": 50})
    result = relay("agent-bus", "GET", f"/turns?{qs}")
    if isinstance(result, dict) and result.get("error"):
        return None
    if isinstance(result, list):
        return [t for t in result if isinstance(t, dict)]
    turns = result.get("turns") if isinstance(result, dict) else None
    if not isinstance(turns, list):
        return None
    return [t for t in turns if isinstance(t, dict)]


def refuse_if_pickup_awaits(
    *,
    subject: str,
    body: str,
    thread: str | None,
    event_prefix: str,
) -> dict[str, Any] | None:
    """Return refusal envelope when pickup declaration/cease gate fails."""
    from claude_bundles.pickup_awaits import (
        coerce_prior_turns,
        is_cease_to_act,
        refusal_envelope,
        validate_pickup_awaits,
    )

    prior = None
    if is_cease_to_act(subject=subject, body=body):
        loaded = load_prior_turns(thread)
        if loaded is not None:
            prior = coerce_prior_turns(loaded)
    verdict = validate_pickup_awaits(
        subject=subject,
        body=body,
        prior_turns=prior,
    )
    if verdict.ok:
        return None
    record(
        f"{event_prefix}.rejected",
        reason=verdict.reason or "pickup_declaration_missing",
    )
    return refusal_envelope(verdict)


__all__ = ["load_prior_turns", "refuse_if_pickup_awaits"]

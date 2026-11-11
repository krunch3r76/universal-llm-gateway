"""Read-only listing of pending restart intents."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .restart_intent_consumer import project_restart_intent_last
from .restart_intent_expiry import effective_expires_at, intent_expired
from .restart_intent_lookup import latest_terminal_for_service


def _list_row(intent: Any, *, now: datetime) -> dict[str, Any]:
    effective = effective_expires_at(intent, now=now)
    expired = intent_expired(intent, now=now)
    ttl_remaining_s: float | None = None
    if effective is not None and not expired:
        ttl_remaining_s = max(0.0, (effective - now).total_seconds())
    expires_iso = effective.astimezone(UTC).isoformat() if effective else None
    return {
        "intent_id": intent.intent_id,
        "service": intent.service,
        "status": intent.status,
        "action": intent.action,
        "caller_agent": intent.caller_agent,
        "armed_at": intent.armed_at,
        "expires_at": expires_iso,
        "ttl_remaining_s": ttl_remaining_s,
        "expired": expired,
        "wait_for_boundary": intent.wait_for_boundary,
        "drain_epoch": intent.drain_epoch,
        "reason": intent.reason,
        "status_reason": intent.status_reason,
    }


def list_restart_intents(
    store: Any,
    *,
    service: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Project pending rows and the newest terminal per touched service."""
    clock = now or datetime.now(UTC)
    rows = store.pending_intents()
    if service:
        rows = [row for row in rows if row.service == service]
    intents = [_list_row(row, now=clock) for row in rows]
    services = {row.service for row in rows}
    if service:
        services.add(service)
    recent_terminal: dict[str, Any] = {}
    for svc in sorted(services):
        terminal = latest_terminal_for_service(store, svc, now=clock)
        recent_terminal[svc] = (
            project_restart_intent_last(terminal) if terminal is not None else None
        )
    return {
        "as_of": clock.astimezone(UTC).isoformat(),
        "intents": intents,
        "recent_terminal": recent_terminal,
    }

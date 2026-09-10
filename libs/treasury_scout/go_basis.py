"""GO basis for treasury-scout relay intents — read from the venue, never invented.

A GO turn or relay intent is judgeable only with ``{fetched_at, expires_at,
book_fingerprint}`` (``decision:status-basis-invariant``). ``book_fingerprint`` is
the server's own value from ``GET /positions``; when the server does not publish
one it stays ``""`` and ``enter_symbol`` refuses with 422 — this side never
fabricates a fingerprint. Expiry is cadence-scaled (three ~10 min scout cycles),
not the six hours the trader used before agent-bus:99998 leg 2A.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

_TS = "%Y-%m-%dT%H:%M:%SZ"


def intent_ttl_s() -> int:
    return int(os.environ.get("TRADER_INTENT_TTL_S", "1800"))


def venue_basis(perps_get: Callable[[str], dict[str, Any]], *, fetched_at: str) -> dict[str, Any]:
    fetched = datetime.strptime(fetched_at, _TS).replace(tzinfo=UTC)
    try:
        fingerprint = str(perps_get("/positions").get("book_fingerprint") or "")
    except Exception:  # noqa: BLE001 — unreadable venue ⇒ empty basis ⇒ server refuses loudly
        fingerprint = ""
    return {
        "fetched_at": fetched_at,
        "expires_at": (fetched + timedelta(seconds=intent_ttl_s())).strftime(_TS),
        "book_fingerprint": fingerprint,
    }


def go_body(intent: dict[str, Any], *, rationale: str, selection: str) -> str:
    """Bus body for a GO turn; carries the basis so a reader can judge staleness from the turn."""
    basis = intent.get("basis") if isinstance(intent.get("basis"), dict) else {}
    return (
        f"TYPE: GO\n"
        f"source: grok-defer\n"
        f"selection: {selection}\n"
        f"intent_id: {intent.get('intent_id') or ''}\n"
        f"symbol: {intent.get('symbol') or ''}\n"
        f"side: {intent.get('side')}\n"
        f"size_usdc: {intent.get('size_usdc')}\n"
        f"fetched_at: {basis.get('fetched_at') or intent.get('fetched_at') or ''}\n"
        f"expires_at: {basis.get('expires_at') or intent.get('expires_at') or ''}\n"
        f"book_fingerprint: {basis.get('book_fingerprint') or ''}\n"
        f"rationale: {rationale}\n"
        f"fire_path: relay when PERPS_TRADER_FIRE=true"
    )


def exposure_check_line(positions: dict[str, Any]) -> str:
    """APPLY line for the leg-4 comparison record (replaces the bare `exposure_mismatch` bool)."""
    check = positions.get("exposure_check") if isinstance(positions.get("exposure_check"), dict) else {}
    return (
        f"  count={positions.get('count')} "
        f"venue_total_usdc={positions.get('venue_total_exposure_usdc')} "
        f"exposure_check venue_entry={check.get('venue_entry_notional_usdc')} "
        f"bot_fill={check.get('bot_fill_notional_usdc')} delta={check.get('delta_usdc')} "
        f"tol={check.get('tolerance_usdc')} mismatch={check.get('mismatch')} as_of={check.get('as_of')}"
    )

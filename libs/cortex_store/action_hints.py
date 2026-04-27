"""Read-time action hint detection for temporal staleness.

Detects assertions and deadlines that need agent action — expired temporal
bounds, overdue deadlines with resolution language in assertion text — and
returns structured ActionHint objects for inclusion in API responses.

Both functions are pure-ish (detect_expired_unresolved needs no DB;
detect_deadline_resolution needs a connection for matter assertion lookup)
and are importable by routes and by Dream State handlers.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

from .db import query
from .models import ActionHint

_RESOLUTION_KEYWORDS = frozenset(
    {
        "resolved",
        "filed",
        "completed",
        "escalated",
        "submitted",
        "sent",
        "defaulted",
        "settled",
        "withdrawn",
        "dismissed",
        "closed",
    }
)


def detect_expired_unresolved(
    assertions: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> list[ActionHint]:
    """Detect unsuperseded assertions with valid_until in the past.

    Pure function — no DB access. Operates on already-fetched assertion
    dicts (or Pydantic model dicts). Returns one ActionHint per stale
    assertion.
    """
    if now is None:
        now = datetime.now(UTC)

    hints: list[ActionHint] = []
    for a in assertions:
        if a.get("superseded_by"):
            continue
        valid_until = a.get("valid_until")
        if not valid_until:
            continue
        try:
            exp = datetime.fromisoformat(str(valid_until).replace("Z", "+00:00"))
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=UTC)
            if exp >= now:
                continue
            days_past = (now - exp).days
            aid = a.get("id") or a.get("assertion_id")
            eid = a.get("entity_id", "")
            claim_preview = (a.get("claim") or "")[:80]
            hints.append(
                ActionHint(
                    category="expired_temporal",
                    target_id=aid,
                    entity_id=eid,
                    message=(
                        f"Assertion {aid} expired {days_past}d ago "
                        f"(valid_until={str(valid_until)[:10]}), still active. "
                        f'Claim: "{claim_preview}..."'
                    ),
                    action=(
                        f'cortex(tool="supersede", arguments=\'{{"old_assertion_id": '
                        f"{aid}, "
                        f'"entity_id": "{eid}", "claim": "...", '
                        f'"confidence": "confirmed", "evidence": "...", '
                        f'"session_id": "...", "agent": "..."}}\')'
                    ),
                )
            )
        except (ValueError, TypeError):
            continue
    return hints


def detect_deadline_resolution(
    deadlines: list[dict[str, Any]],
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
) -> list[ActionHint]:
    """Detect overdue deadlines where the matter's assertions suggest resolution.

    For each deadline past its date, queries the matter entity's latest active
    assertions for resolution-language keywords. One hint per overdue deadline
    whose matter already has resolution language in Cortex.
    """
    if now is None:
        now = datetime.now(UTC)
    today = now.date()

    hints: list[ActionHint] = []
    for d in deadlines:
        dl_date_str = d.get("deadline_date") or ""
        if not dl_date_str:
            continue
        try:
            dl_date = datetime.strptime(str(dl_date_str)[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if dl_date >= today:
            continue

        matter_id = d.get("matter_id", "")
        if not matter_id:
            continue

        matter_assertions = query(
            conn,
            "SELECT id, claim FROM assertions "
            "WHERE entity_id = ? AND superseded_by IS NULL "
            "ORDER BY created_at DESC LIMIT 5",
            (matter_id,),
        )

        deadline_id = d.get("deadline_id", "")
        for a in matter_assertions:
            claim_lower = (a.get("claim") or "").lower()
            matches = [kw for kw in _RESOLUTION_KEYWORDS if kw in claim_lower]
            if matches:
                days_overdue = (today - dl_date).days
                hints.append(
                    ActionHint(
                        category="overdue_deadline_resolved",
                        target_id=a["id"],
                        entity_id=matter_id,
                        message=(
                            f"Deadline '{d.get('deadline_name', '?')}' is "
                            f"{days_overdue}d overdue, but assertion {a['id']} "
                            f"on {matter_id} mentions: {', '.join(matches)}. "
                            f"Use deadline_resolve to close it."
                        ),
                        action=(
                            f'cortex(tool="deadline_resolve", arguments=\'{{"deadline_id": '
                            f'"{deadline_id}", "resolution_note": "...", '
                            f'"resolved_at": "YYYY-MM-DDTHH:MM:SSZ"}}\')'
                        ),
                    )
                )
                break
    return hints

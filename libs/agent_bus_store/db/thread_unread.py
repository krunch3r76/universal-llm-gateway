"""Thread-list unread aggregate: unstamped non-superseded turns, optional seat."""

from __future__ import annotations

from typing import Any

from ..recipients import recipient_in_clause

UNREAD_BASIS_SOURCE = "agent_bus_store.threads"
UNREAD_BASIS_KIND = "read_at_null"


def unread_count_sql(*, recipient: str | None) -> tuple[str, list[str]]:
    """SQL expression + binds for ``unread_count`` on ``tu`` (LEFT JOIN alias).

    Excludes superseded turns so the thread-wide count matches TOC/triage.
    ``recipient`` reuses ``fetch_unread``'s ``include_team`` rule.
    """
    extras = ""
    params: list[str] = []
    if recipient is not None:
        clause, params = recipient_in_clause(
            recipient, include_team=recipient != "kaywan"
        )
        extras = " AND " + clause.replace("to_agent", "tu.to_agent")
    expr = (
        "COALESCE(SUM(CASE WHEN tu.read_at IS NULL "
        f"AND tu.status != 'superseded'{extras} THEN 1 ELSE 0 END), 0)"
    )
    return expr, params


def unread_basis(*, recipient: str | None, as_of: str) -> dict[str, Any]:
    """Status-basis envelope for ``unread_count`` (status-basis invariant)."""
    return {
        "basis": UNREAD_BASIS_KIND,
        "recipient": recipient,
        "includes_superseded": False,
        "as_of": as_of,
        "source": UNREAD_BASIS_SOURCE,
    }


def attach_unread_basis(
    rows: list[dict[str, Any]], *, recipient: str | None, as_of: str
) -> None:
    envelope = unread_basis(recipient=recipient, as_of=as_of)
    for row in rows:
        row["unread_basis"] = dict(envelope)

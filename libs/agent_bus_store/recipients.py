"""Recipient slug expansion for turn inbox queries."""

from __future__ import annotations

from agent_seat.registry import expand_recipient_slugs


def recipient_in_clause(seat: str, *, include_team: bool) -> tuple[str, list[str]]:
    """Build ``to_agent IN (...)`` SQL fragment and bind values for a seat."""
    recipients = expand_recipient_slugs(seat)
    extras = ("all", "team") if include_team else ("all",)
    placeholders = ",".join("?" * (len(recipients) + len(extras)))
    clause = f"(to_agent IN ({placeholders}))"
    return clause, [*recipients, *extras]

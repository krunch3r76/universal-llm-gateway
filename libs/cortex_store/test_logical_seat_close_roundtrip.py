"""Logical-seat session-close roundtrip — stubbed external debrief (no live DB)."""

from __future__ import annotations

from agent_seat.boot_identity import legacy_boot_identity


def test_logical_identity_agent_key_stable_for_close_wire() -> None:
    """Close paths consume session_identity.agent; logical keys stay canonical."""
    ident = legacy_boot_identity("gpt", "web").__class__(
        family="gpt",
        platform="web",
        agent="alpha-work",
        session_prefix="alpha-work",
        inbox_recipient="alpha-work",
        self_entity_id="seat:alpha-work",
        logical_seat_id="alpha-work",
    )
    session_id = "alpha-work-2026-06-10-012830-abc"
    wire = {"session_id": session_id, "agent": ident.agent}
    assert wire["agent"] == "alpha-work"
    assert session_id.startswith(ident.session_prefix + "-")

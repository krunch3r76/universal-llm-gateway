"""Shared continuity-hop handoff body and standing-handoff freshness.

Cadence (GIW) and the MCP ``hop`` verb both import from here so the structural
``TYPE: CONTINUITY_HANDOFF`` body has one author. Domain isolation: neither
mcp-server nor git_integration_worker imports the other.
"""

from __future__ import annotations

from hop_handoff.body import (
    build_continuity_handoff_body,
    build_seat_registration_stamp,
    build_seat_stand_down_body,
    is_successor_birth_id,
    mint_successor_birth_id,
    parse_successor_birth_id,
)
from hop_handoff.consume_protocol import consume_time_wake_protocol
from hop_handoff.standing_handoff import (
    StandingHandoffFreshness,
    assess_standing_handoff,
    cse_age_threshold_s,
    standing_handoff_path,
    standing_handoff_uri,
)

# Harvest nominates these manage slugs when this lib lands (package-grain).
CONSUMERS: tuple[str, ...] = ('git_integration_worker', 'mcp')

__all__ = [
    "StandingHandoffFreshness",
    "assess_standing_handoff",
    "build_continuity_handoff_body",
    "build_seat_registration_stamp",
    "build_seat_stand_down_body",
    "consume_time_wake_protocol",
    "cse_age_threshold_s",
    "is_successor_birth_id",
    "mint_successor_birth_id",
    "parse_successor_birth_id",
    "standing_handoff_path",
    "standing_handoff_uri",
]

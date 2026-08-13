"""Shared continuity-hop handoff body and standing-handoff freshness.

Cadence (GIW) and the MCP ``hop`` verb both import from here so the structural
``TYPE: CONTINUITY_HANDOFF`` body has one author. Domain isolation: neither
mcp-server nor git_integration_worker imports the other.
"""

from __future__ import annotations

from hop_handoff.body import build_continuity_handoff_body
from hop_handoff.standing_handoff import (
    StandingHandoffFreshness,
    assess_standing_handoff,
    cse_age_threshold_s,
    standing_handoff_path,
    standing_handoff_uri,
)

__all__ = [
    "StandingHandoffFreshness",
    "assess_standing_handoff",
    "build_continuity_handoff_body",
    "cse_age_threshold_s",
    "standing_handoff_path",
    "standing_handoff_uri",
]

"""Agent → default transcript depth for close-draft mint (H-* table membership)."""

from __future__ import annotations

from typing import Literal

TranscriptDepthDefault = Literal["light", "verbatim", "none"]

# Ship empty (= H-B): Kaywan populates speech-hosting agents when H-A/H-C chosen.
SPEECH_SEAT_AGENTS: frozenset[str] = frozenset()


def default_depth_for_agent(agent: str) -> TranscriptDepthDefault:
    """Return default close-draft depth for *agent*."""
    if agent in SPEECH_SEAT_AGENTS:
        return "verbatim"
    return "light"


__all__ = ["SPEECH_SEAT_AGENTS", "TranscriptDepthDefault", "default_depth_for_agent"]

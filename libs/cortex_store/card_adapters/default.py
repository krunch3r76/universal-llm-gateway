"""Universal fallback adapter — used when entity type has no registered adapter."""

from __future__ import annotations

from .base import BaseCardAdapter


class DefaultAdapter(BaseCardAdapter):
    type_name = "default"

    expected_section_ids = (
        "assertions",
        "assertions_superseded",
        "relationships",
        "archives_to",
        "reasoning_edges",
    )

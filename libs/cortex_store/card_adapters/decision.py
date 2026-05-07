"""Decision adapter — confidence/status-centric semantics."""

from __future__ import annotations

from .base import BaseCardAdapter


class DecisionAdapter(BaseCardAdapter):
    type_name = "decision"

    expected_section_ids = (
        "assertions",
        "assertions_superseded",
        "relationships",
        "archives_to",
        "reasoning_edges",
    )

    label_assertions = "Reasoning (active)"
    label_assertions_superseded = "Reasoning (superseded)"
    label_relationships = "Decision links"
    label_archives_to = "Archived into"
    label_reasoning_edges = "Reasoning edges"

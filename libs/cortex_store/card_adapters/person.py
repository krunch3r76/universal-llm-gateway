"""Person adapter — relationship-graph-centric semantics."""

from __future__ import annotations

from .base import BaseCardAdapter


class PersonAdapter(BaseCardAdapter):
    type_name = "person"

    expected_section_ids = (
        "assertions",
        "assertions_superseded",
        "relationships",
        "archives_to",
        "reasoning_edges",
    )

    label_assertions = "Observations (active)"
    label_assertions_superseded = "Observations (superseded)"
    label_relationships = "Connections"
    label_archives_to = "Archived into"
    label_reasoning_edges = "Reasoning edges"

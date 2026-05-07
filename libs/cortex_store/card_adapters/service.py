"""Service adapter — operational status semantics."""

from __future__ import annotations

from .base import BaseCardAdapter


class ServiceAdapter(BaseCardAdapter):
    type_name = "service"

    expected_section_ids = (
        "assertions",
        "assertions_superseded",
        "relationships",
        "archives_to",
        "reasoning_edges",
    )

    label_assertions = "Operational notes (active)"
    label_assertions_superseded = "Operational notes (superseded)"
    label_relationships = "Service links"
    label_archives_to = "Archived into"
    label_reasoning_edges = "Reasoning edges"

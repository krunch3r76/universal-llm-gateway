"""Case adapter — case-file semantics (estate, mortgage, legal matters)."""

from __future__ import annotations

from .base import BaseCardAdapter


class CaseAdapter(BaseCardAdapter):
    type_name = "case"

    expected_section_ids = (
        "assertions",
        "assertions_superseded",
        "relationships",
        "archives_to",
        "reasoning_edges",
    )

    label_assertions = "Case findings (active)"
    label_assertions_superseded = "Case findings (superseded)"
    label_relationships = "Parties & matters"
    label_archives_to = "Archived into"
    label_reasoning_edges = "Reasoning edges"

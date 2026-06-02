"""Document adapter — ingestion-status-centric semantics."""

from __future__ import annotations

from typing import Any

from .base import BaseCardAdapter


class DocumentAdapter(BaseCardAdapter):
    type_name = "document"

    expected_section_ids = (
        "assertions",
        "assertions_superseded",
        "relationships",
        "archives_to",
        "reasoning_edges",
    )

    label_assertions = "Extracted claims (active)"
    label_assertions_superseded = "Extracted claims (superseded)"
    label_relationships = "Document references"
    label_archives_to = "Archived into"
    label_reasoning_edges = "Reasoning edges"

    def status_summary(self, entity: dict[str, Any]) -> dict[str, Any] | None:
        from ..status_trait_read import card_status_summary_option_c

        return card_status_summary_option_c(
            entity,
            extra={
                "source_uri": entity.get("source_uri"),
                "content_hash": entity.get("content_hash"),
                "updated_at": entity.get("updated_at"),
            },
        )

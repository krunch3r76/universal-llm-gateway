"""Card adapter contract (Cortex v2.4 §6.4).

The card builder fetches identity columns + counts in a projection-aware
fetch plan, then hands those off to a per-entity-type adapter that names
the section_manifest, picks status semantics, and selects the
summary_row. The transport schema is uniform across adapters; the
labels and status fields vary by entity type.

Adapters MUST NOT touch the database — they receive the entity row plus
counts already materialized by the card builder. This keeps the
projection-aware-fetch invariant (§6.2) intact: SQL stays in card.py;
adapters operate on already-loaded data.
"""

from __future__ import annotations

from typing import Any, ClassVar, TypedDict

from ..models import CardSection


class CardAdapterCounts(TypedDict):
    """Counts materialized by the card builder, passed to the adapter."""

    active_n: int
    superseded_n: int
    rel_total: int
    archives_to_count: int
    edges_n: int


class BaseCardAdapter:
    """Default adapter — universal fallback.

    Subclasses override ``label_*`` class attributes for type-specific
    section labels, and may override ``status_summary`` / ``summary_row``
    to specialize per-type semantics.

    §6.4 stance (provisional, see ``decision:cortex-v24-card-section-uniformity``):
    the section ids currently emitted by every adapter are the same five
    (assertions, assertions_superseded, relationships, archives_to,
    reasoning_edges). §6.4 reserves per-type latitude on which sections are
    meaningful and how counts are computed; Slice 2 ships the plausible
    minimum because no concrete consumer yet exercises divergent per-type
    section selection. The contract is binding *per adapter*: subclasses
    redeclare ``expected_section_ids`` explicitly so future per-type
    divergence (anticipated at Slice 3 ``predicate_form`` or Slice 4
    ``predicate_summary`` aggregation) is a one-line edit on the diverging
    adapter, not a framework change.
    """

    type_name: ClassVar[str] = "default"

    expected_section_ids: ClassVar[tuple[str, ...]] = (
        "assertions",
        "assertions_superseded",
        "relationships",
        "archives_to",
        "reasoning_edges",
    )

    label_assertions: ClassVar[str] = "Assertions (active)"
    label_assertions_superseded: ClassVar[str] = "Assertions (superseded)"
    label_relationships: ClassVar[str] = "Relationships"
    label_archives_to: ClassVar[str] = "Archives"
    label_reasoning_edges: ClassVar[str] = "Reasoning edges"

    def sections(
        self, entity: dict[str, Any], counts: CardAdapterCounts
    ) -> list[CardSection]:
        return [
            CardSection(
                id="assertions", label=self.label_assertions, count=counts["active_n"]
            ),
            CardSection(
                id="assertions_superseded",
                label=self.label_assertions_superseded,
                count=counts["superseded_n"],
            ),
            CardSection(
                id="relationships",
                label=self.label_relationships,
                count=counts["rel_total"],
            ),
            CardSection(
                id="archives_to",
                label=self.label_archives_to,
                count=counts["archives_to_count"],
            ),
            CardSection(
                id="reasoning_edges",
                label=self.label_reasoning_edges,
                count=counts["edges_n"],
            ),
        ]

    def status_summary(self, entity: dict[str, Any]) -> dict[str, Any] | None:
        return {
            "status": entity.get("status"),
            "workflow_state": entity.get("workflow_state"),
            "updated_at": entity.get("updated_at"),
        }

    def summary_row(self, entity: dict[str, Any]) -> str | None:
        desc = entity.get("description")
        return str(desc) if desc is not None else None

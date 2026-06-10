"""Entity-shape Pydantic models, including the Card v0 read projection.

Cross-section deps:
  * ``AssertionItem`` and ``ActionHint`` / ``CompactionProjection`` live in
    ``.assertions`` — referenced by ``EntityDetail``.
  * ``RelationshipItem`` lives in ``.relationships`` — referenced by
    ``EntityDetail``.
  * ``EdgeItem`` lives in ``.edges`` — referenced by ``EntityDetail``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from ._shared import AssertionConfidence, reject_cortex_dropbox_source_uri
from .assertions import ActionHint, AssertionItem, CompactionProjection
from .edges import EdgeItem
from .relationships import RelationshipItem


class _EntityCommon(BaseModel):
    aliases: list[str] | None = None
    attributes: dict[str, Any] | None = None
    notes: str | None = None
    source_uri: str | None = None
    content_hash: str | None = None

    _validate_source_uri = field_validator("source_uri")(
        reject_cortex_dropbox_source_uri
    )


# Confidence axis: unsubstantiated/provisional/confirmed — DERIVED from backing
# assertions under Fork D (G1, thread 1173). `unsubstantiated` is the birth
# default; `confirmed` is no longer hand-settable (see entity_crud freeze).
# Lifecycle axis: merged/deprecated/reaped. Full split into record_state +
# substantiation_state is full-D, out of D-core scope.
EntityStatus = Literal[
    "unsubstantiated", "confirmed", "provisional", "merged", "deprecated", "reaped"
]
RetentionPolicy = Literal["permanent", "ephemeral", "archival"]


class EntityCreate(_EntityCommon):
    id: str
    type: str
    name: str
    description: str | None = None
    status: EntityStatus | None = None
    workflow_state: str | None = None
    retention_policy: RetentionPolicy | None = None
    retention_ttl_days: int | None = None


class EntitySummary(BaseModel):
    id: str
    type: str
    name: str
    description: str | None = None
    # Option C read: synthesized display string (not raw ``entities.status`` alone).
    status: str | None = None
    lifecycle: str | None = None
    confidence_band: str | None = None
    adoption: str | None = None
    workflow_state: str | None = None
    content_hash: str | None = None
    created_at: str


class EntityDetail(_EntityCommon):
    id: str
    type: str
    name: str
    description: str | None = None
    status: str | None = None
    lifecycle: str | None = None
    confidence_band: str | None = None
    adoption: str | None = None
    workflow_state: str | None = None
    created_at: str
    updated_at: str
    assertions: list[AssertionItem] = Field(default_factory=list)
    relationships: list[RelationshipItem] = Field(default_factory=list)
    reasoning_edges: list[EdgeItem] = Field(default_factory=list)
    action_hints: list[ActionHint] | None = None
    compaction_projection: CompactionProjection | None = None


class EntityUpdate(BaseModel):
    name: str | None = None
    aliases: list[str] | None = None
    attributes: dict[str, Any] | None = None
    notes: str | None = None
    source_uri: str | None = None
    description: str | None = None
    status: EntityStatus | None = None
    workflow_state: str | None = None
    content_hash: str | None = None
    retention_policy: RetentionPolicy | None = None
    retention_ttl_days: int | None = None

    _validate_source_uri = field_validator("source_uri")(
        reject_cortex_dropbox_source_uri
    )


class EntityList(BaseModel):
    items: list[EntitySummary]


# --- v2.4 read model: intent-shaped projections ---

EntityIntent = Literal["full", "card", "cluster", "impact"]


class CardAssertion(BaseModel):
    """Compact assertion projection embedded in Card v0 top-K list."""

    id: int
    claim: str
    confidence: AssertionConfidence
    derivation_type: str | None = None
    valid_from: str | None = None
    observed_at: str | None = None
    evidence_uris: list[str] | None = None
    entrenchment_score: float | None = None


class CardEdgeTypeCount(BaseModel):
    type_id: str
    count: int


class CardSection(BaseModel):
    id: str
    label: str
    count: int


class CardDebug(BaseModel):
    """§7.8 observability: emitted only when `?debug=1`."""

    fetch_plan_row_volume: int
    prospective_summaries: list[str | None] | None = None


class EntityCard(BaseModel):
    """Card v0 payload (v2.4 §6.3)."""

    intent: Literal["card"] = "card"
    id: str
    type: str
    name: str
    summary_row: str | None = None
    status_summary: dict[str, Any] | None = None
    top_k_assertions: list[CardAssertion] = Field(default_factory=list)
    edge_type_summary: list[CardEdgeTypeCount] = Field(default_factory=list)
    archives_to_count: int = 0
    section_manifest: list[CardSection] = Field(default_factory=list)
    predicate_summary: str = ""
    freshness: dict[str, str] | None = None
    debug: CardDebug | None = None

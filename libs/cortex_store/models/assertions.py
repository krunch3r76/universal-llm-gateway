"""Assertion-shape Pydantic models — write/read/search/impact surfaces."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from agent_seat.session_id import derive_session_id_from_timestamp
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..attributes_coerce import coerce_attributes_input
from ._shared import (
    AssertionConfidence,
    reject_cortex_dropbox_source_uri,
    reject_cortex_dropbox_uri_list,
)

DerivationType = Literal[
    "quotation",
    "compression",
    "thread_compression",
    "inference",
    "direct_observation",
    "agent_observation",
    "user_statement",
    "stated",
    "commitment",
    "other",
]

ResolutionStatus = Literal["pending", "fulfilled", "breached", "unknown"]
ReviewStatus = Literal["committed", "flagged", "staged", "rejected"]


class AssertionCreate(BaseModel):
    entity_id: str
    claim: str
    confidence: AssertionConfidence = Field(
        description="One of: confirmed, believed, suspected, hypothesized"
    )
    evidence: str
    evidence_uris: list[str] | None = None
    seeded_by: str | None = None
    chunk_id: str | None = None  # RAG-deterministic ID: {content_hash_prefix}-{i}
    chunk_id_schema: str | None = None  # 'rag_deterministic' | 'legacy_cortex' | None
    derivation_type: DerivationType | None = None
    reasoning_summary: str | None = None
    observed_at: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    confidence_score: float | None = None
    is_atomic: bool = True
    is_decontextualized: bool = True
    resolution_status: ResolutionStatus | None = None
    fulfillment_assertion_id: int | None = None
    # v3 enrichment writeback — populated by pipelines/assertion_enrichment/.
    prospective_summary: str | None = None
    events_json: str | None = None
    artifact_uri: str | None = None
    artifact_storage: str = "inline"
    # C2: explicit revision bypass — force=True skips contradiction check,
    # supersedes_id marks the target for atomic supersession
    force: bool = Field(
        False,
        description="Bypass C2 contradiction check",
    )
    supersedes_id: int | None = Field(
        None,
        description=(
            "Assertion to supersede. Requires force=True to chain lineage — "
            "without force, the target's superseded_by is NOT set and the new "
            "assertion is created as a sibling (a protocol validation_warning "
            "is returned). Prefer the `supersede` op for atomic close+create."
        ),
    )
    # Auditor-validatability opt-outs: pass one or more of
    # ['no_evidence_uris', 'inference_confirmed', 'no_verbatim'] to suppress
    # the corresponding advisory warning when confidence='confirmed'.
    # Suppression is explicit — each gap must be acknowledged individually.
    acknowledge_audit_gaps: list[str] | None = Field(
        None,
        description=(
            "Suppress specific auditor-validatability warnings on "
            "confidence:confirmed assertions. Valid values: "
            "'no_evidence_uris', 'inference_confirmed', 'no_verbatim'. "
            "See agent_skill:auditor-validatable-confidence."
        ),
    )
    # v1.3 Q5: optional caller seed — when provided, the route handler
    # normalizes via normalize_predicate_domain() before INSERT and stores
    # the canonical_form. When absent, the field stays NULL on INSERT and
    # the async predicate-extract pipeline populates it later. Additive —
    # existing callers are unaffected (None default, no BC shim).
    predicate_form: str | None = None
    # F1 (1211): per-assertion metadata JSON, primarily Menu D consensus
    # disposition stamps (consensus-steelman-posture §3.1 — assertion SOT).
    attributes: dict[str, Any] | None = None
    dry_run: bool = Field(
        False,
        description=(
            "When true, run pre-INSERT validation and return validation_warnings "
            "without creating an assertion row."
        ),
    )

    _validate_artifact_uri = field_validator("artifact_uri")(
        reject_cortex_dropbox_source_uri
    )
    _validate_evidence_uris = field_validator("evidence_uris")(
        reject_cortex_dropbox_uri_list
    )

    @field_validator("attributes", mode="before")
    @classmethod
    def _coerce_attributes(cls, value: object) -> dict[str, Any] | None:
        return coerce_attributes_input(value)

    @field_validator("predicate_form")
    @classmethod
    def _validate_predicate_form(cls, v: str | None) -> str | None:
        if v is not None:
            if not v.strip():
                raise ValueError(
                    "predicate_form must be a non-empty string when provided"
                )
            if len(v) > 2000:
                raise ValueError("predicate_form must not exceed 2000 characters")
        return v


class AssertionItem(BaseModel):
    id: int
    entity_id: str | None = None
    claim: str
    confidence: AssertionConfidence
    confidence_score: float | None = None
    evidence: str | None = None
    evidence_uris: list[str] | None = None
    seeded_by: str | None = None
    derivation_type: DerivationType | None = None
    chunk_id: str | None = None  # RAG-deterministic ID: {content_hash_prefix}-{i}
    chunk_id_schema: str | None = None  # 'rag_deterministic' | 'legacy_cortex' | None
    reasoning_summary: str | None = None
    is_atomic: bool | None = None
    is_decontextualized: bool | None = None
    observed_at: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    superseded_by: int | None = None
    review_status: ReviewStatus | None = None
    reviewer: str | None = None
    reviewed_at: str | None = None
    review_notes: str | None = None
    resolution_status: str | None = None
    fulfillment_assertion_id: int | None = None
    quality_score: float | None = None
    # v3 enrichment writeback — populated by pipelines/assertion_enrichment/.
    prospective_summary: str | None = None
    events_json: str | None = None
    artifact_uri: str | None = None
    artifact_storage: str | None = None
    entrenchment_score: float | None = None
    # v2.4 Slice 3: peer projection of `claim` for FOL-B retrieval (§6.7).
    # NULL = not yet extracted; populated by pipelines/predicate_extract/.
    predicate_form: str | None = None
    created_at: str
    # v1.3.1 normalization-decision ledger (read-side only; write-once via
    # create/supersede paths, never via AssertionUpdate PATCH).
    raw_predicate_form: str | None = None
    normalization_decision: str | None = None
    candidate_set_fingerprint: str | None = None
    normalizer_version: str | None = None
    # F1 (1211): per-assertion metadata JSON, primarily Menu D consensus
    # disposition stamps (consensus-steelman-posture §3.1 — assertion SOT).
    attributes: dict[str, Any] | None = None


class AssertionUpdate(BaseModel):
    superseded_by: int | None = None
    # Fill-only: patchable when the row has no anchor, guarded against moving one.
    valid_from: str | None = None
    valid_until: str | None = None
    confidence: AssertionConfidence | None = None
    confidence_score: float | None = None
    review_status: ReviewStatus | None = None
    reviewer: str | None = None
    reviewed_at: str | None = None
    review_notes: str | None = None
    resolution_status: ResolutionStatus | None = None
    fulfillment_assertion_id: int | None = None
    # v2.4 Slice 3: peer projection writeback path. Populated by
    # pipelines/predicate_extract/ after fire-and-forget dispatch from the
    # assertion-create write hook. Explicit null in PATCH body clears the field.
    predicate_form: str | None = None
    # v3 enrichment writeback — populated by pipelines/assertion_enrichment/.
    prospective_summary: str | None = None
    events_json: str | None = None
    # Idempotency guard for superseded_by writes. When the target row already
    # has a non-null superseded_by, the PATCH is rejected with 409 Conflict
    # unless force=True is set. Prevents silent lineage clobber from
    # concurrent or duplicated supersession passes. See todo:
    # cortex-superseded-by-overwrite-guards / friction 9824, 9825.
    force: bool = False

    @field_validator("predicate_form")
    @classmethod
    def _validate_predicate_form(cls, v: str | None) -> str | None:
        if v is not None:
            if not v.strip():
                raise ValueError(
                    "predicate_form must be a non-empty string when provided"
                )
            if len(v) > 2000:
                raise ValueError("predicate_form must not exceed 2000 characters")
        return v


RevisionType = Literal["restatement", "correction", "status_update"]


class SupersedeRequest(BaseModel):
    old_assertion_id: int
    entity_id: str
    claim: str
    confidence: AssertionConfidence
    evidence: str
    # Optional override fields — when absent from the caller's payload they are
    # inherited from the superseded assertion (fix-path b: clone-then-override).
    # Callers that want to intentionally drop a field should pass explicit null.
    evidence_uris: list[str] | None = None
    valid_from: str | None = None
    derivation_type: DerivationType | None = None
    reasoning_summary: str | None = None
    seeded_by: str | None = None
    chunk_id: str | None = None  # RAG-deterministic ID: {content_hash_prefix}-{i}
    confidence_score: float | None = None
    # Optional at call time — defaulted for session_edges bookkeeping when omitted
    # (matches assert ergonomics; friction 22843).
    session_id: str | None = None
    agent: str | None = None
    # Clone-then-override: predicate_form is carried over from the superseded
    # assertion unless explicitly supplied here. Pass explicit null to
    # intentionally drop the field on the new row. When supplied and non-null,
    # the route normalises via normalize_predicate_domain() before INSERT.
    # Fixes friction 9826 / todo:cortex-supersede-predicate-form-carryover.
    predicate_form: str | None = None
    # Idempotency guard for the old assertion's superseded_by field. When the
    # target old assertion already has a non-null superseded_by, the supersede
    # call is rejected with 409 Conflict unless force=True is set. Prevents
    # silent lineage clobber from concurrent or duplicated supersession
    # passes. See todo:cortex-superseded-by-overwrite-guards / friction 9824.
    force: bool = False
    revision_type: RevisionType | None = None
    attributes: dict[str, Any] | None = None
    # Auditor-validatability opt-outs (same semantics as AssertionCreate).
    acknowledge_audit_gaps: list[str] | None = Field(
        None,
        description=(
            "Suppress specific auditor-validatability warnings. Valid values: "
            "'no_evidence_uris', 'inference_confirmed', 'no_verbatim'. "
            "See agent_skill:auditor-validatable-confidence."
        ),
    )

    _validate_evidence_uris = field_validator("evidence_uris")(
        reject_cortex_dropbox_uri_list
    )

    @field_validator("attributes", mode="before")
    @classmethod
    def _coerce_attributes(cls, value: object) -> dict[str, Any] | None:
        return coerce_attributes_input(value)

    @model_validator(mode="before")
    @classmethod
    def _default_session_context(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        payload = dict(data)
        if not payload.get("agent"):
            payload["agent"] = payload.get("seeded_by") or "unknown"
        if not payload.get("session_id"):
            payload["session_id"] = derive_session_id_from_timestamp(
                payload["agent"], datetime.now(UTC).isoformat()
            )
        return payload


class SupersedeResponse(BaseModel):
    old: AssertionItem
    new: AssertionItem
    impact_warning: str | None = None
    # Advisory auditor-validatability warnings — see check_confirmed_validatability().
    # Non-null only when confidence='confirmed' and one or more checks fire.
    validation_warnings: list[dict[str, str]] | None = None


class NearDuplicateWarning(BaseModel):
    existing_id: int
    score: float


class ContradictionConflict(BaseModel):
    """A conflicting assertion detected by C2 write-path contradiction check."""

    assertion_id: int
    claim: str
    confidence: str
    similarity: float


class ActionHint(BaseModel):
    """Structured hint for data that likely needs agent action.

    Attached to read responses when temporal staleness or unresolved state
    is detected. Nudges agents to close the loop on stale data.
    """

    category: str
    target_id: int | None = None
    entity_id: str | None = None
    message: str
    action: str


class PredicateFormNormalize(BaseModel):
    """Result of v1.3 predicate_form normalize-on-write.

    Surfaced on assertion create/update responses when the caller seeded a
    ``predicate_form`` and the route ran ``normalize_predicate_domain`` over it.
    The MCP dispatcher layer reads this field and emits
    ``mcp.cortex.predicate.normalized`` (always) and
    ``mcp.cortex.predicate.review.required`` (when ``requires_human_review`` is
    True). cortex-api itself stays HTTP-only — no ``mcp_events`` dep — so the
    emission surface lives at the dispatcher contract layer, matching the
    sibling-family invariant for ``mcp.cortex.assertion.*`` signals (per
    assertion 10259 / Q5.5 deferral, dispatch packet
    ``cortex://notes/system/threads/cortex-api-event-emission-surface-dispatch.md``).
    """

    predicate_form_in: str
    canonical_form: str
    classes_applied: list[int] = Field(default_factory=list)
    normalized: bool
    requires_human_review: bool


class AssertionCreateResponse(BaseModel):
    was_new: bool
    item: AssertionItem | None = None
    dry_run: bool = False
    would_write: bool | None = None
    near_duplicate_warning: NearDuplicateWarning | None = None
    validation_warnings: list[dict[str, str]] | None = None
    contradiction_warnings: list[ContradictionConflict] | None = None
    predicate_form_normalize: PredicateFormNormalize | None = None
    already_known: bool = False
    known_state_reason: str | None = None
    matched_assertion_id: int | None = None


class AssertionUpdateResponse(BaseModel):
    """Envelope for PATCH /assertions/{id}.

    Wraps ``item`` (the post-update row) with optional
    ``predicate_form_normalize`` so the MCP dispatcher layer can emit
    ``mcp.cortex.predicate.normalized`` / ``.review.required`` without
    a new dep into cortex-api routes. See ``PredicateFormNormalize``.
    """

    item: AssertionItem
    predicate_form_normalize: PredicateFormNormalize | None = None


class CompactionProjection(BaseModel):
    """Metadata emitted when §6.10 compaction-pointer projection was applied.

    Present on ``AssertionList`` and ``EntityDetail`` when the response was
    reordered or collapsed due to compaction-pointer detection.  ``None`` when
    no compaction pattern was found or when ``include_compaction_pointers=True``
    was passed (raw-stream mode).
    """

    mode: str  # "pointers_deprioritized" | "tombstone_collapsed" | "aggregate_pointers_excluded"
    pointer_count: int
    summary_count: int = 0
    children: list[str] = Field(default_factory=list)
    navigation_hint: str | None = None


class AssertionListSummaryItem(BaseModel):
    """Sparse list hook — invites deepen via assertion_get."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    id: int
    entity_id: str | None = None
    claim: str
    confidence: AssertionConfidence
    review_status: str | None = None
    derivation_type: DerivationType | None = None
    observed_at: str | None = None
    superseded_by: int | None = None
    has_evidence_uris: bool = False
    has_enrichment: bool = False
    deepen: str | None = Field(default=None, alias="_deepen")


class AssertionList(BaseModel):
    intent: Literal["summary", "full"] = "summary"
    items: list[AssertionListSummaryItem | AssertionItem]
    action_hints: list[ActionHint] | None = None
    compaction_projection: CompactionProjection | None = None


class TouchedAssertionItem(BaseModel):
    """Assertion touched by a proposed claim in C1 impact analysis."""

    assertion_id: int
    claim: str
    confidence: str
    similarity: float
    entity_id: str
    retrieval_source: str


class ImpactAnalysisRequest(BaseModel):
    entity_id: str
    claim: str
    confidence: AssertionConfidence = "believed"


class ImpactAnalysisResponse(BaseModel):
    touched_assertions: list[TouchedAssertionItem]
    likely_supersedes: list[int]
    implicated_entities: list[str]
    impact_score: float


class EnrichRequest(BaseModel):
    enrichments: list[str] | None = Field(
        None,
        description='Enrichment kinds to run: "prospective", "events". '
        "Defaults to all if omitted.",
    )


class EnrichResponse(BaseModel):
    item: AssertionItem
    enrichments_run: list[str]
    results: dict[str, str | None]

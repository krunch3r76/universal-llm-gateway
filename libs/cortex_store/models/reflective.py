"""Reflective-journal Pydantic models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# ``"handoff"`` is retained in the read-side Literal so historical
# ``reflective_journal`` rows (kind="handoff" entries written before
# ``decision:rj-handoff-kind-retirement`` landed on 2026-05-27) continue
# to deserialize via ``ReflectiveEntryItem.model_validate`` without
# ``ValidationError``. The write surface in ``routes/reflective_journal.py``
# narrows ``_VALID_KINDS`` to ``{"entry", "reflection", "revision",
# "consolidation"}`` — new writes via ``POST /reflective-journal`` or via
# ``_insert_reflective_entry_tx(kind="handoff", ...)`` get rejected.
# The DB CHECK constraint (migration 033) still permits ``'handoff'``
# values for the same read-compat reason. The same asymmetry applies to
# ``"handoff_for"`` in ``JournalLinkType`` — historical link rows remain
# queryable; no new ones get written after Phase 3 of plan
# session-close-handoff-session-journals.
ReflectiveKind = Literal["entry", "reflection", "revision", "consolidation", "handoff"]
JournalLinkType = Literal[
    "contradicts",
    "refines",
    "supersedes",
    "reopens",
    "unresolved_with",
    "continues",
    "related",
    "handoff_for",
]


class JournalLinkCreate(BaseModel):
    to_entry: int | None = None
    to_entity: str | None = None
    link_type: JournalLinkType


class JournalLinkItem(BaseModel):
    id: int
    from_entry: int
    to_entry: int | None = None
    to_entity: str | None = None
    link_type: JournalLinkType
    created_at: str


class ConsolidationData(BaseModel):
    """Structured consolidation synthesis with anti-coherence-theater safeguards."""

    throughline: str
    before: str
    now: str
    tension_points: list[str] = Field(default_factory=list)
    contradiction_set: list[str] = Field(default_factory=list)
    falsifier: str | None = None
    rendered_shift: str | None = None
    confidence: str | None = None
    source_entry_ids: list[int] = Field(default_factory=list)


class ReflectiveEntryCreate(BaseModel):
    agent: str
    register: str
    entry: str
    kind: ReflectiveKind = "entry"
    session_id: str | None = None
    revises: int | None = None
    links: list[JournalLinkCreate] | None = None
    consolidation_data: ConsolidationData | None = None


class ReflectiveEntryItem(BaseModel):
    id: int
    agent: str
    register: str
    entry: str
    kind: ReflectiveKind
    session_id: str | None = None
    revises: int | None = None
    consolidation_data: dict[str, Any] | None = None
    links: list[JournalLinkItem] = Field(default_factory=list)
    suggested_links: list[dict[str, Any]] | None = None
    created_at: str


class ReflectiveEntryList(BaseModel):
    items: list[ReflectiveEntryItem]
    total: int

"""Request bodies for S6 typed friction/observe substrate routes."""

from __future__ import annotations

from pydantic import BaseModel


class ObserveRequest(BaseModel):
    entity_id: str | None = None
    claim: str | None = None
    confidence: str = "believed"
    agent: str | None = None
    evidence: str | None = None


class FrictionCreateRequest(BaseModel):
    owner: str | None = None
    service: str | None = None
    category: str | None = None
    note: str | None = None
    claim: str | None = None
    suggestion: str | None = None
    agent: str | None = None
    session_id: str | None = None
    charter_root: str | None = None
    window_index: int | None = None
    root_thread: str | None = None
    cp_ordinal: int | None = None
    scoreboard_uri: str | None = None
    actionable: bool | None = None
    actionable_false_reason: str | None = None
    checkpoint_turn: int | None = None
    evidence_uris: list[str] | str | None = None
    defer_enqueue: bool | None = None
    confidence: str | None = None
    confidence_score: float | None = None


class FrictionCloseRequest(BaseModel):
    resolution_kind: str | None = None
    agent: str | None = None
    session_id: str | None = None
    evidence: str | None = None
    resolution_note: str | None = None
    changed_paths: list[str] | None = None


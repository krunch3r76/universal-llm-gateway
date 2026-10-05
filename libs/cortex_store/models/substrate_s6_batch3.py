"""Request bodies for S6 batch 3 typed substrate routes (tags / RJ / deadlines)."""

from __future__ import annotations

from pydantic import BaseModel


class RjConsolidateRequest(BaseModel):
    agent: str | None = None
    register: str | None = None
    entry: str | None = None
    session_id: str | None = None
    throughline: str | None = None
    before: str | None = None
    now: str | None = None
    tension_points: list[str] | None = None
    contradiction_set: list[str] | None = None
    falsifier: str | None = None
    rendered_shift: str | None = None
    confidence: str | None = None
    source_entry_ids: list[int] | None = None


class DeadlineResolveRequest(BaseModel):
    resolution_note: str | None = None
    resolved_at: str | None = None
    evidence: str | None = None
    fulfilling_assertion_id: int | None = None
    outcome: str = "met"


"""Request/response models for concern aggregate routes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ConcernTransitionRequest(BaseModel):
    concern_id: str
    kind: str
    record_ref: str
    authorized_by: str
    payload: dict[str, Any] | None = None
    intent_id: str | None = None
    request_id: str | None = None
    delivery_id: str | None = None
    emit_advisory: bool = True
    expected_revision: int | None = None
    write_intent_id: str | None = None
    material: dict[str, Any] | None = None


class ConcernTransitionResponse(BaseModel):
    concern_id: str
    seq: int
    record_ref: str
    revision: int
    fencing_generation: int
    through_seq: int
    write_intent_id: str | None = None
    replayed: bool | None = None


class ConcernRebuildRequest(BaseModel):
    concern_id: str
    checkpoint: bool = False


class ConcernRebuildResponse(BaseModel):
    concern_id: str
    state: dict[str, Any] = Field(default_factory=dict)

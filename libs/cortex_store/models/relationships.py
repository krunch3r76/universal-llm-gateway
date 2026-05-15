"""Relationship-shape Pydantic models."""

from __future__ import annotations

from pydantic import BaseModel, field_validator

from ._shared import reject_cortex_dropbox_source_uri


class RelationshipCreate(BaseModel):
    source_id: str
    target_id: str
    type_id: str
    role: str | None = None
    strength: float | None = None
    evidence: str | None = None
    chunk_id: int | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    source_uri: str | None = None
    session_id: str | None = None
    agent: str | None = None

    _validate_source_uri = field_validator("source_uri")(
        reject_cortex_dropbox_source_uri
    )


class RelationshipItem(BaseModel):
    id: int
    source_id: str
    target_id: str
    type_id: str
    type_name: str | None = None
    source_name: str | None = None
    target_name: str | None = None
    role: str | None = None
    strength: float | None = None
    evidence: str | None = None
    chunk_id: int | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    source_uri: str | None = None
    session_id: str | None = None
    agent: str | None = None
    created_at: str


class RelationshipCreateResponse(BaseModel):
    was_new: bool
    item: RelationshipItem


class RelationshipList(BaseModel):
    items: list[RelationshipItem]


class RelationshipUpdate(BaseModel):
    """Mutable fields of a relationship. At least one must be supplied.

    Source, target, and type define relationship identity — to correct those,
    delete the relationship and recreate with the correct values.
    """

    role: str | None = None
    strength: float | None = None
    evidence: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    source_uri: str | None = None
    session_id: str | None = None
    agent: str | None = None

    _validate_source_uri = field_validator("source_uri")(
        reject_cortex_dropbox_source_uri
    )


class RelationshipDeleteResponse(BaseModel):
    deleted: bool
    id: int

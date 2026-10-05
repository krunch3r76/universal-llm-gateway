"""Request bodies for S6 batch 4 typed substrate routes (bulk upserts)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .entities import EntityCreate


class BulkEntityUpsertItem(EntityCreate):
    """One row for ``entities_bulk_upsert`` — same fields as dispatch plus per-item ``if_exists``."""

    model_config = ConfigDict(extra="allow")

    if_exists: str | None = None


class EntitiesBulkUpsertRequest(BaseModel):
    entities: list[BulkEntityUpsertItem] | None = None
    if_exists: str = "fail"


class BulkRelationshipUpsertItem(BaseModel):
    """One row for ``relationships_bulk_upsert`` — dispatch fields plus per-item overrides."""

    model_config = ConfigDict(extra="allow")

    source_id: str | None = None
    target_id: str | None = None
    type_id: str | None = None
    role: str | None = None
    strength: float | None = None
    evidence: str | None = None
    chunk_id: int | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    source_uri: str | None = None
    session_id: str | None = None
    agent: str | None = None
    if_exists: str | None = None
    resolve_aliases: bool | None = None


class RelationshipsBulkUpsertRequest(BaseModel):
    relationships: list[BulkRelationshipUpsertItem] | None = None
    if_exists: str = "fail"
    resolve_aliases: bool = True


# Re-export entity field literals for OpenAPI parity with EntityCreate.
__all__ = [
    "BulkEntityUpsertItem",
    "BulkRelationshipUpsertItem",
    "EntitiesBulkUpsertRequest",
    "RelationshipsBulkUpsertRequest",
]

"""Pydantic response schemas for graph traversal endpoints.

Models for GET /edges/impact (C1 reverse-dependency BFS) and
GET /assertions/activate (C3 spreading activation).  The POST
/assertions/analyze-impact (semantic impact) lives in ``assertions.py``.
"""

from __future__ import annotations

from pydantic import BaseModel


class ImpactedEntityItem(BaseModel):
    entity_id: str
    entity_name: str | None
    hop_distance: int
    path_trace: list[str]
    assertion_count: int
    edge_types: list[str]
    substrates: list[str]


class ImpactResponse(BaseModel):
    seed_entity: str
    depth: int
    impacted_entities: list[ImpactedEntityItem]
    total_impacted_assertions: int


class ActivatedAssertionItem(BaseModel):
    assertion_id: int
    entity_id: str
    claim: str
    confidence: str
    entrenchment_score: float
    activation_score: float
    hop_distance: int
    activation_path: list[str]
    edge_types_traversed: list[str]
    substrates_traversed: list[str]


class ActivateResponse(BaseModel):
    seed_entities: list[str]
    depth: int
    hub_suppression: bool
    count: int
    activated: list[ActivatedAssertionItem]

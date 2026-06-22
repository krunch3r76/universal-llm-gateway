"""Session-edge Pydantic models."""

from __future__ import annotations

from pydantic import BaseModel


class EdgeCreate(BaseModel):
    session_id: str
    agent: str
    from_node: str
    to_node: str
    edge_type: str
    strength: float = 0.8
    edge_source: str = "explicit"
    context: str | None = None
    prompt: str | None = None
    seeded_by: str | None = None
    metadata: str | None = None


class EdgeItem(BaseModel):
    id: int
    session_id: str
    agent: str
    from_node: str
    to_node: str
    edge_type: str
    strength: float
    edge_source: str
    context: str | None
    prompt: str | None
    seeded_by: str | None
    valid_until: str | None
    metadata: str | None
    created_at: str


class EdgeList(BaseModel):
    items: list[EdgeItem]
    count: int


class EdgeRetire(BaseModel):
    valid_until: str | None = None  # None = now()


class EdgeUpdate(BaseModel):
    """Mutable scalar fields of an active session-edge. At least one must be supplied.

    Identity (from_node/to_node/edge_type), provenance (session_id/agent/seeded_by),
    and retirement (valid_until — owned by edge_retire) are NOT patchable here.
    """

    strength: float | None = None
    context: str | None = None
    prompt: str | None = None
    metadata: str | None = None

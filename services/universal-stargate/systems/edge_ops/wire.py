"""Request bodies for ``/api/v1/edge/*``.

Kept apart from the route handlers so the HTTP shape reads in one place. The
field names follow the dispatch design's resource table; ``kinds`` replaces
that table's ``displays``/``health`` pair because A1 leases by op kind and the
edge opens no window.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class AdmitBody(BaseModel):
    """``POST /api/v1/edge/ops`` — queue one op for an edge to pick up."""

    kind: str = Field(..., min_length=1)
    op: str = Field(..., min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(..., min_length=1)
    deadline_s: float | None = None


class LeaseBody(BaseModel):
    """``POST /api/v1/edge/leases`` — the edge's own long-poll for work."""

    kinds: list[str] = Field(..., min_length=1)
    edge_id: str | None = None
    wait_s: float | None = None


class EventBody(BaseModel):
    """``POST /api/v1/edge/ops/{id}/events`` — open type plus free payload."""

    type: str = Field(..., min_length=1)
    lease_id: str = Field(..., min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)


class ResultBody(BaseModel):
    """``POST /api/v1/edge/ops/{id}/result`` — the handler result, verbatim.

    ``outcome`` is the terminal state and is deliberately separate from the
    result: a handler that answered ``{"ok": false}`` posts
    ``outcome="completed"``, and ``failed`` is for an exception or an
    infrastructure error.
    """

    lease_id: str = Field(..., min_length=1)
    result: Any = None
    outcome: Literal["completed", "failed"] = "completed"


class ReleaseBody(BaseModel):
    """``POST /api/v1/edge/ops/{id}/release`` — end the lease without a result."""

    lease_id: str = Field(..., min_length=1)

"""Request bodies for S6 batch 8 typed substrate routes."""

from __future__ import annotations

from pydantic import BaseModel, Field


class EntityRetypeRequest(BaseModel):
    new_type: str
    force: bool = False


class EndeavorRepairT1Request(BaseModel):
    """No persisted fields — repair is global T1 host maintenance."""

    model_config = {"extra": "forbid"}

"""Request bodies for S6 batch 9 typed substrate routes."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ViewRenderRequest(BaseModel):
    mode: str = "refresh"
    root_id: str | None = None
    view_profile: str | None = None
    narrative_sections: dict[str, str] | None = None
    as_of_system: str | None = None
    as_of_valid: str | None = None
    agent: str | None = None
    session_id: str | None = None

    model_config = {"extra": "ignore"}

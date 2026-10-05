"""Request bodies for S6 batch 7 typed substrate routes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RegisterSkillSubstrateRequest(BaseModel):
    skill_id: str
    skill_path: str
    case_id: str | None = None
    description: str = ""
    trigger_phrases: list[str] | None = None
    skill_binding: dict[str, Any] | None = None
    session_id: str | None = None
    agent: str | None = None


class ViewRenderRequest(BaseModel):
    """All fields accepted by ``_op_view_render`` (handler default ``mode=refresh``)."""

    mode: str = "refresh"
    root_id: str | None = None
    view_profile: str | None = None
    narrative_sections: dict[str, str] | None = None
    as_of_system: str | None = None
    as_of_valid: str | None = None
    agent: str | None = None
    session_id: str | None = None

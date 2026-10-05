"""Request bodies for S6 batch 6 typed substrate routes (endeavor rows + view render)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class EndeavorWriteRowRequest(BaseModel):
    host: str
    fields: dict[str, Any]


class EndeavorDisposeRowRequest(BaseModel):
    host: str
    row_id: str
    disposition: str
    reason: str | None = None
    authority: str | None = None


class ViewRenderRequest(BaseModel):
    mode: str = "refresh"
    root_id: str | None = None
    view_profile: str | None = None
    narrative_sections: dict[str, str] | None = None
    as_of_system: str | None = None
    as_of_valid: str | None = None
    agent: str | None = None
    session_id: str | None = None

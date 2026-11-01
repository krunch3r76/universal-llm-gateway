"""Request/response models for ``/v1/display-chat/paste``."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DisplayChatPasteRequest(BaseModel):
    display: str = Field(..., min_length=1)
    app: Literal["grok"] = "grok"
    agent_id: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)
    grant: Literal["operator", "explicit"]
    authorization_source: str = Field(..., min_length=1)
    scope: str = ""
    select: bool = True


class DisplayChatPasteResponse(BaseModel):
    ok: bool
    code: str | None = None
    reason: str | None = None
    display: str | None = None
    app: str | None = None
    agent_id: str | None = None
    delivered: bool = False
    readback_ok: bool = False
    sha256_dom: str | None = None
    sha256_png: str | None = None
    composer_empty_after: bool | None = None
    assistant_began: bool | None = None
    watermark_visible: bool | None = None
    locate_method: str | None = None

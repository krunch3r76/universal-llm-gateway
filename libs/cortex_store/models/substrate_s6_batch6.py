"""Request bodies for S6 batch 6 typed substrate routes (endeavor strategy rows)."""

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

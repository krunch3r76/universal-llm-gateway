"""Extraction-run Pydantic models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator

from ._shared import reject_cortex_dropbox_source_uri


class ExtractionCheckRequest(BaseModel):
    source_uri: str
    content_hash: str

    _validate_source_uri = field_validator("source_uri")(
        reject_cortex_dropbox_source_uri
    )


class ExtractionCheckResponse(BaseModel):
    action: Literal["proceed", "skip", "re-extract"]
    run_id: int
    superseded_run_id: int | None = None
    superseded_assertion_count: int | None = None


class ExtractionRunComplete(BaseModel):
    status: Literal["completed", "failed"] = "completed"
    assertion_count: int = 0


class ExtractionRunItem(BaseModel):
    id: int
    source_uri: str
    content_hash: str | None = None
    status: str
    assertion_count: int | None = None
    created_at: str
    completed_at: str | None = None

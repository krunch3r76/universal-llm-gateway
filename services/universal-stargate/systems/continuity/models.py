"""Pydantic request/response models for Stargate continuity routes."""

from __future__ import annotations

from typing import Any, Literal

from continuity_tape.messages import ContinuityMessagesEnvelope, Tools
from pydantic import BaseModel, Field


class TapeReadRequest(BaseModel):
    thread: str
    scope: Literal["last_session", "full"] = "full"
    include_extras: bool = False
    tools: Tools = "none"
    budget_bytes: int = Field(default=512_000, ge=1024, le=8_000_000)
    harvest: bool = False


class TapeReadEnvelopeResponse(ContinuityMessagesEnvelope):
    """Door 1 sync read — ``open_line`` first on the wire via route serializer."""


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


TapeReadResponse = TapeReadEnvelopeResponse | dict[str, Any]

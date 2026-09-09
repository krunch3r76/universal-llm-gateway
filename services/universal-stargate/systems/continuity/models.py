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


class CheckpointRequest(BaseModel):
    thread: str
    surface: Literal["cursor", "claude_ai"]
    from_agent: str
    transcript_id: str | None = None
    jsonl_path: str | None = None
    chat_url: str | None = None
    residue: str | None = Field(default=None, max_length=800)
    pre_consolidate: bool = True
    tools: Tools = "none"
    pipeline_options: dict[str, Any] | None = None


class CheckpointAccepted(BaseModel):
    execution_id: str
    pipeline: str
    thread: str
    status: Literal["running"] = "running"
    started_at: str
    poll_hint: dict[str, Any]


class TapeReadEnvelopeResponse(ContinuityMessagesEnvelope):
    """Door 1 sync read — ``open_line`` first on the wire via route serializer."""


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


TapeReadResponse = TapeReadEnvelopeResponse | dict[str, Any]

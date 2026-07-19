"""Request/response models for the CDP project-ask satellite."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ExecutionStatus = Literal["pending", "running", "completed", "failed", "aborted"]


class SubmitProjectAskRequest(BaseModel):
    prompt_text: str | None = None
    prompt_uri: str | None = None
    prompt_path: str | None = None
    holder: str = Field(default="cdp-ask-satellite")
    purpose: str = "ask"
    model: str = "opus-4.8"
    converse: bool = False
    no_project_uuid: bool = False
    project_uuid: str = ""
    ensure_cowork_auto: bool = True
    archive_path: str | None = None
    timeout_s: int = 360
    min_body: int = 40
    min_growth: int = 50
    delete_after: bool | None = None


class ExecutionSummary(BaseModel):
    execution_id: str
    status: ExecutionStatus
    registration_id: str | None = None
    created_at: float
    updated_at: float


class SubmitProjectAskResponse(BaseModel):
    execution_id: str
    status: ExecutionStatus
    registration_id: str | None = None


class ExecutionPollResponse(BaseModel):
    execution_id: str
    status: ExecutionStatus
    registration_id: str | None = None
    ok: bool | None = None
    archive_uri: str | None = None
    body: str | None = None
    body_len: int | None = None
    url: str | None = None
    project_uuid: str | None = None
    project_url: str | None = None
    model: dict[str, Any] | None = None
    attested_model: str | None = None
    error: str | None = None
    delete_after: dict[str, Any] | None = None
    results: list[dict[str, Any]] | None = None


class AbortExecutionResponse(BaseModel):
    execution_id: str
    status: ExecutionStatus
    aborted: bool
    attested: bool = False
    still_attached: bool | None = None
    abort_outcome: str = ""
    stop_clicked: bool | None = None

"""Request bodies for S6 batch 5 typed substrate routes (sidecar / deliverable writes)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ReconSidecarWriteRequest(BaseModel):
    label: str
    theme: str
    body: str
    scopes: list[str] | None = None
    queries: list[str] | None = None
    sink_backend: str | None = None


class ThreadSidecarWriteRequest(BaseModel):
    thread: str
    subject: str
    content: str
    from_agent: str | None = None
    execution_id: str | None = None
    oversized: bool = False
    sidecar_slug: str | None = None


class TodoCloseSidecarRequest(BaseModel):
    todo_id: str | None = None
    summary: str | None = None
    evidence: str | None = None
    reasoning_summary: str | None = None
    references: list[dict[str, Any]] | None = None
    agent: str | None = None
    session_id: str | None = None
    closed_at: str | None = None


class PinnedDeliverableWriteRequest(BaseModel):
    rel_path: str
    content: str
    write_if_absent: bool | None = None
    dispatch_id: str | None = None
    thread_id: str | None = None

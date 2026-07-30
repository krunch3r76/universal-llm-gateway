"""Pydantic models for ``POST /api/v1/cursor/dispatch``."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


class CursorDispatchRequest(BaseModel):
    """Dispatch admission body — ``packet_path`` XOR ``message``."""

    model_config = ConfigDict(extra="forbid")

    thread_id: str
    model: str
    dispatch_id: str
    execution_id: str
    caller_agent: str | None = None
    packet_path: str | None = None
    message: str | None = None
    handoff_contract: str | None = None
    prompt_preamble: str | None = None
    model_knobs: dict[str, str] | None = None
    read_only: bool = False
    close_contract: Literal["lead", "auto"] = "auto"
    force: bool = False
    source_ref: str | None = None
    nest_under: str | None = None
    refuse_if_lease_held: bool = False
    worktree_isolated: bool = False
    worktree_path: str | None = None
    admitted_via: Literal["cursor-auto", "stargate"] | None = None

    @model_validator(mode="after")
    def _packet_xor_message(self) -> CursorDispatchRequest:
        has_packet = bool(self.packet_path)
        has_message = bool(self.message)
        if has_packet == has_message:
            raise ValueError("exactly one of packet_path or message is required")
        return self


class CursorDispatchResponse(BaseModel):
    """Admission acknowledgement returned before background SDK run."""

    admitted: bool
    dispatch_id: str
    thread_id: str
    model_id: str
    status: Literal["admitted", "queued"] = "admitted"
    queue_position: int | None = None
    since: str | None = None
    holder_dispatch_id: str | None = None
    holder_thread_id: str | None = None
    holder_resolved_model: str | None = None
    holder_subject_preview: str | None = None
    holder_status: str | None = None
    holder_started_at: str | None = None
    holder_last_heartbeat_at: str | None = None

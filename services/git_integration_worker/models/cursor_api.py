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

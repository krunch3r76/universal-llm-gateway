"""Pydantic models for ``POST /api/v1/cursor/dispatch``."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CursorDispatchRequest(BaseModel):
    """Dispatch admission body — ``packet_path`` XOR ``message``."""

    model_config = ConfigDict(extra="forbid")

    thread_id: str
    model: str
    dispatch_id: str
    execution_id: str
    request_id: str | None = None
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
    lane: Literal["A", "B"] | None = None
    worktree_isolated: bool = False
    worktree_path: str | None = Field(
        default=None,
        description=(
            "Branch-targeting wire surface: a checked-out worktree path becomes the "
            "write lease_key. Same path serializes per I2; distinct paths may run in "
            "parallel under the derived standard load pool. There is no branch= arg."
        ),
    )
    admitted_via: Literal["cursor-auto", "stargate"] | None = None
    work_key: str | None = None
    resume_of: str | None = None

    @model_validator(mode="after")
    def _resume_of_consistency(self) -> CursorDispatchRequest:
        if self.resume_of and self.nest_under:
            raise ValueError("resume_of and nest_under are mutually exclusive")
        if self.resume_of and self.resume_of == self.dispatch_id:
            raise ValueError("resume_of must not equal dispatch_id")
        return self

    @model_validator(mode="after")
    def _packet_xor_message(self) -> CursorDispatchRequest:
        has_packet = bool(self.packet_path)
        has_message = bool(self.message)
        if has_packet == has_message:
            raise ValueError("exactly one of packet_path or message is required")
        return self

    @model_validator(mode="after")
    def _lane_wire_consistency(self) -> CursorDispatchRequest:
        if self.lane == "A" and (self.worktree_isolated or self.worktree_path):
            raise ValueError(
                "lane='A' is incompatible with worktree_isolated/worktree_path"
            )
        return self


class BranchDischargeRequest(BaseModel):
    """Explicit retirement of a lane branch — the closeout's declared outcome."""

    branch: str
    verb: Literal["landed", "discard"]
    reason: str | None = None


class CursorDispatchResponse(BaseModel):
    """Admission acknowledgement returned before background SDK run."""

    admitted: bool
    dispatch_id: str
    thread_id: str
    model_id: str
    status: Literal["admitted", "queued", "cancelled", "completed", "failed"] = (
        "admitted"
    )
    queue_position: int | None = None
    since: str | None = None
    holder_dispatch_id: str | None = None
    holder_thread_id: str | None = None
    holder_resolved_model: str | None = None
    holder_subject_preview: str | None = None
    holder_status: str | None = None
    holder_started_at: str | None = None
    holder_last_heartbeat_at: str | None = None
    # Branch obligation, surfaced at admit so the contract and the lane's
    # outstanding residue are both visible before any work is done.
    lane_branch: str | None = None
    lane_open_debts: int | None = None
    lane_debt_branches: list[str] | None = None

"""Extraction-staging Pydantic models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

StagingStatus = Literal["pending", "approved", "rejected", "merged"]
ProposalType = Literal["entity", "assertion"]
ProposalAction = Literal["add", "revise", "remove"]


class StagingProposalCreate(BaseModel):
    source_uri: str | None = None
    proposal_type: ProposalType
    proposal_action: ProposalAction = "add"
    target_id: str | None = None
    proposal_json: dict[str, Any]
    chunk_id: int | None = None


class StagingBatchCreate(BaseModel):
    proposals: list[StagingProposalCreate]


class StagingItem(BaseModel):
    id: int
    source_uri: str | None = None
    proposal_type: ProposalType
    proposal_action: ProposalAction
    target_id: str | None = None
    proposal_json: dict[str, Any] | None = None
    chunk_id: int | None = None
    status: StagingStatus
    resolved_to: str | None = None
    reviewer: str | None = None
    reviewed_at: str | None = None
    created_at: str


class StagingList(BaseModel):
    items: list[StagingItem]


class StagingApproval(BaseModel):
    reviewer: str = "human"

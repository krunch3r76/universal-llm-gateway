"""Seat-claim Pydantic models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

SeatClaimStatus = Literal["held", "released", "reclaimed"]
SeatClaimEndReason = Literal["released", "stale", "superseded"]


class SeatClaimRequest(BaseModel):
    claim_key: str
    seat: str
    ttl_s: float | None = None
    metadata: dict[str, Any] | None = None


class SeatClaimGranted(BaseModel):
    granted: Literal[True] = True
    holder_id: str
    claim_key: str
    seat: str
    claimed_at: str
    ttl_s: float


class SeatClaimHolder(BaseModel):
    seat: str
    holder_id: str
    claimed_at: str
    last_heartbeat_at: str | None = None
    age_s: float
    expires_in_s: float


class SeatClaimDenied(BaseModel):
    granted: Literal[False] = False
    holder: SeatClaimHolder


class SeatClaimResponse(BaseModel):
    granted: bool
    holder_id: str | None = None
    claim_key: str | None = None
    seat: str | None = None
    claimed_at: str | None = None
    ttl_s: float | None = None
    holder: SeatClaimHolder | None = None


class SeatHeartbeatRequest(BaseModel):
    holder_id: str


class SeatHeartbeatResponse(BaseModel):
    ok: bool
    last_heartbeat_at: str | None = None
    expires_in_s: float = 0.0


class SeatReleaseRequest(BaseModel):
    holder_id: str


class SeatReleaseResponse(BaseModel):
    released: bool
    end_reason: SeatClaimEndReason | None = None


class SeatClaimRow(BaseModel):
    id: int
    claim_key: str
    seat: str
    holder_id: str
    status: SeatClaimStatus
    claimed_at: str
    last_heartbeat_at: str | None = None
    ttl_s: float
    ended_at: str | None = None
    end_reason: SeatClaimEndReason | None = None
    metadata: dict[str, Any] | None = None


class SeatClaimsListResponse(BaseModel):
    claims: list[SeatClaimRow] = Field(default_factory=list)

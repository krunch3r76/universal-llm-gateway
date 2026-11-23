"""POST /boot-mint — session id mint without briefing card fetch/render."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from agent_seat.boot_identity import (
    BootIdentity,
    boot_identity_to_wire,
    resolve_boot_identity_for_seat,
)
from agent_seat.session_id import SessionMintMode, mint_session_id
from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(tags=["boot"])
_LA = ZoneInfo("America/Los_Angeles")

_HOW_TO_FULL_BRIEF = (
    "For the full briefing card, call cortex_brief without mint_only=true."
)


class BootMintRequest(BaseModel):
    seat: str = Field(..., min_length=1, description="Seat slug or capability cell")


def perform_boot_mint(
    seat: str,
    *,
    at: datetime | None = None,
) -> dict[str, Any]:
    """Mint a live session id and wire metadata for *seat* without boot fetches."""
    resolved = resolve_boot_identity_for_seat(seat)
    if isinstance(resolved, dict):
        return resolved
    return _mint_payload(resolved, at=at or datetime.now(UTC))


def _mint_payload(identity: BootIdentity, *, at: datetime) -> dict[str, Any]:
    session_id = mint_session_id(
        identity.session_prefix,
        mode=SessionMintMode.LIVE,
        at=at,
    )
    return {
        "session_id": session_id,
        "session_identity": {"session_id": session_id, "agent": identity.agent},
        "boot_identity": boot_identity_to_wire(identity),
        "mode": "live",
        "utc_now": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "local_time": at.astimezone(_LA).strftime("%Y-%m-%dT%H:%M:%S%z"),
        "mint_only": True,
        "briefing_card_hint": _HOW_TO_FULL_BRIEF,
    }


@router.post("/boot-mint")
def boot_mint(body: BootMintRequest) -> dict[str, Any]:
    """Mint session id + boot identity without briefing card side effects."""
    return perform_boot_mint(body.seat)

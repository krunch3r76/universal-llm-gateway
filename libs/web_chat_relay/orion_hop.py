"""Hop Orion to a new chatgpt.com conversation (holder refresh is out-of-band)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OrionHopRequest:
    thread: str
    reason: str
    reapprove_connector: bool = True


def build_hop_body(req: OrionHopRequest) -> str:
    return (
        f"HOP — agent-bus thread {req.thread}. Reason: {req.reason}. "
        "Open a new chatgpt.com conversation, re-allow ulg-code for this "
        "conversation only (second approval option), then post "
        "SEAT_REGISTRATION with the new /c/ URL on the bus."
    )


def hop_receipt(*, ok: bool, reason: str, new_url: str | None = None) -> dict[str, Any]:
    return {"ok": ok, "reason": reason, "new_conversation_url": new_url}

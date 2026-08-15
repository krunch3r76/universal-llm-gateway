"""Followup response envelope primitives — typed failures and candidate shape.

A leaf module: the identity ladder (``followup_resolve``) and the attended-seat
mapping (``followup_attended``) both build these, so neither may own them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cdp_ask.models import (
    FollowupCandidateInfo,
    FollowupProjectAskRequest,
    FollowupProjectAskResponse,
    TargetBinding,
)

REGISTRY_SOURCE = "cse-session-registry"

_CLI_ESCAPE = "scripts/cortex/cowork_chat_followup.py"
_HORIZON = "v1 requires an attached lane; post-deregister reattach is horizon"

__all__ = [
    "REGISTRY_SOURCE",
    "FollowupCandidate",
    "fail_followup",
    "identity_keys",
    "identity_supplied",
    "lane_not_attached_detail",
]


def lane_not_attached_detail() -> str:
    """Human hint for missing/detached lanes including the SSH CLI escape path."""
    return (
        f"{_HORIZON}. CLI escape: {_CLI_ESCAPE} "
        "(list-lanes → curl :port/json/list → SSH paste)."
    )


def fail_followup(
    error: str,
    *,
    detail: str | None = None,
    candidates: list[FollowupCandidateInfo] | None = None,
    send_verified: bool = False,
    **extra: Any,
) -> FollowupProjectAskResponse:
    """Build a typed ``ok=false`` followup response with optional detail fields."""
    return FollowupProjectAskResponse(
        ok=False,
        error=error,
        detail=detail,
        candidates=candidates,
        send_verified=send_verified,
        **extra,
    )


def identity_keys(
    req: FollowupProjectAskRequest,
) -> tuple[str | None, str | None, str | None, str | None]:
    """Return normalized identity keys used by the shared provenance resolver."""
    chat = (req.chat_url or "").strip() or None
    reg = (req.registration_id or "").strip() or None
    exe = (req.execution_id or "").strip() or None
    cdp = (req.cdp_url or "").strip() or None
    return chat, reg, exe, cdp


def identity_supplied(req: FollowupProjectAskRequest) -> bool:
    """True when any explicit identity or port override is present."""
    chat_url, registration_id, execution_id, cdp_url = identity_keys(req)
    return any((chat_url, registration_id, execution_id, cdp_url))


@dataclass(frozen=True)
class FollowupCandidate:
    registration_id: str
    chat_url: str
    holder: str
    purpose: str | None
    cdp_url: str
    target_binding: TargetBinding = "explicit"
    provenance: dict[str, Any] | None = None

    def as_info(self) -> FollowupCandidateInfo:
        return FollowupCandidateInfo(
            registration_id=self.registration_id,
            chat_url=self.chat_url,
            holder=self.holder,
            purpose=self.purpose,
            cdp_url=self.cdp_url,
            source=REGISTRY_SOURCE,
            provenance=self.provenance,
        )

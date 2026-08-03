"""Identity resolution ladder for warm CSE followup on attached CDP lanes.

Reads ``cdp_registry`` snapshots only — never registers lanes, opens profiles,
or navigates to CSE URLs. Fail-closed typed errors; ``ambiguous_identity``
returns candidate rows for disambiguation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from claude_bundles import cdp_registry
from playwright.async_api import async_playwright

from cdp_ask.execution_store import ExecutionStore
from cdp_ask.models import (
    FollowupCandidateInfo,
    FollowupProjectAskRequest,
    FollowupProjectAskResponse,
)

_CLI_ESCAPE = "scripts/cortex/cowork_chat_followup.py"
_HORIZON = "v1 requires an attached lane; post-deregister reattach is horizon"


def normalize_cse_url(url: str) -> str:
    """Normalize CSE URLs for exact comparison (strip fragment, trailing slash)."""
    raw = (url or "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw)
    path = parts.path.rstrip("/") or parts.path
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


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
) -> tuple[str | None, str | None, str | None]:
    """Return normalized ``(chat_url, registration_id, execution_id)`` identity triple."""
    chat = (req.chat_url or "").strip() or None
    reg = (req.registration_id or "").strip() or None
    exe = (req.execution_id or "").strip() or None
    return chat, reg, exe


@dataclass(frozen=True)
class FollowupCandidate:
    registration_id: str
    chat_url: str
    holder: str
    purpose: str | None
    cdp_url: str

    def as_info(self) -> FollowupCandidateInfo:
        return FollowupCandidateInfo(
            registration_id=self.registration_id,
            chat_url=self.chat_url,
            holder=self.holder,
            purpose=self.purpose,
        )


async def scan_lane_cse_urls(reg: cdp_registry.Registration) -> list[str]:
    """List normalized ``/cowork/cse_`` page URLs currently open on one attached lane."""
    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.connect_over_cdp(reg.cdp_url)
        if not browser.contexts:
            return []
        ctx = browser.contexts[0]
        urls: list[str] = []
        for page in ctx.pages:
            url = page.url or ""
            if "/cowork/cse_" in url:
                urls.append(normalize_cse_url(url))
        return urls
    finally:
        await pw.stop()


async def resolve_execution_registration(
    execution_id: str,
    store: ExecutionStore,
) -> str | None:
    """Map a satellite ``execution_id`` to its ``registration_id`` when present."""
    rec = await store.get(execution_id)
    if rec is None or not rec.registration_id:
        return None
    return rec.registration_id


async def discover_candidates(
    req: FollowupProjectAskRequest,
    store: ExecutionStore,
) -> tuple[list[FollowupCandidate], str | None, str | None]:
    """Discover attached CSE targets matching the request identity keys."""
    chat_url, registration_id, execution_id = identity_keys(req)
    if not any((chat_url, registration_id, execution_id)):
        return [], None, None

    mapped_reg: str | None = None
    resolution_path: str | None = None
    if execution_id and not registration_id:
        mapped_reg = await resolve_execution_registration(execution_id, store)
        if mapped_reg is None:
            return [], None, execution_id
        registration_id = mapped_reg
        resolution_path = "execution_id"

    lanes = list(cdp_registry.list_active())
    if chat_url:
        scan_lanes = lanes
    elif registration_id:
        scan_lanes = [lane for lane in lanes if lane.registration_id == registration_id]
        if not scan_lanes:
            return [], resolution_path or "registration_id", execution_id
        if not resolution_path:
            resolution_path = "registration_id"
    elif mapped_reg:
        scan_lanes = [lane for lane in lanes if lane.registration_id == mapped_reg]
        if not scan_lanes:
            return [], resolution_path or "execution_id", execution_id
    else:
        scan_lanes = lanes

    candidates: list[FollowupCandidate] = []
    for lane in scan_lanes:
        try:
            urls = await scan_lane_cse_urls(lane)
        except Exception:
            continue
        for url in urls:
            if chat_url and normalize_cse_url(chat_url) != url:
                continue
            if req.purpose and lane.purpose != req.purpose:
                continue
            candidates.append(
                FollowupCandidate(
                    registration_id=lane.registration_id,
                    chat_url=url,
                    holder=lane.holder,
                    purpose=lane.purpose,
                    cdp_url=lane.cdp_url,
                )
            )

    if chat_url and not resolution_path:
        resolution_path = "chat_url"
    return candidates, resolution_path, execution_id


def conflicting_keys(
    req: FollowupProjectAskRequest,
    chosen: FollowupCandidate,
    *,
    mapped_reg: str | None,
) -> bool:
    """True when supplied identity keys disagree on the chosen lane target."""
    chat_url, registration_id, execution_id = identity_keys(req)
    if chat_url and registration_id and chosen.registration_id != registration_id:
        if stale_registration_id_conflict(req, chosen):
            return False
        return True
    if (
        chat_url
        and execution_id
        and mapped_reg
        and chosen.registration_id != mapped_reg
    ):
        return True
    if (
        registration_id
        and execution_id
        and mapped_reg
        and mapped_reg != registration_id
    ):
        return True
    return False


def stale_registration_id_conflict(
    req: FollowupProjectAskRequest,
    chosen: FollowupCandidate,
) -> bool:
    """True when chat_url uniquely identifies ``chosen`` but ``registration_id`` is stale.

    Arm-time registration can rot across idle windows; fire-time ``chat_url``
    discovery with exactly one live candidate is authoritative.
    """
    chat_url, registration_id, execution_id = identity_keys(req)
    if not chat_url or not registration_id:
        return False
    if chosen.registration_id == registration_id:
        return False
    if execution_id:
        return False
    return True


async def resolve_followup_target(
    req: FollowupProjectAskRequest,
    store: ExecutionStore,
) -> tuple[FollowupCandidate | None, FollowupProjectAskResponse | None, str | None]:
    """Resolve to a single attached CSE target or return a typed error response."""
    chat_url, registration_id, execution_id = identity_keys(req)
    if not any((chat_url, registration_id, execution_id)):
        return None, fail_followup("no_identity"), None

    mapped_reg = None
    if execution_id and not registration_id:
        mapped_reg = await resolve_execution_registration(execution_id, store)
        if mapped_reg is None:
            return (
                None,
                fail_followup("lane_not_attached", detail=lane_not_attached_detail()),
                None,
            )

    candidates, resolution_path, _ = await discover_candidates(req, store)
    if not candidates:
        if chat_url:
            return None, fail_followup("cse_not_found_on_lane"), resolution_path
        return (
            None,
            fail_followup("lane_not_attached", detail=lane_not_attached_detail()),
            resolution_path,
        )

    if len(candidates) > 1:
        infos = [c.as_info() for c in candidates]
        return (
            None,
            fail_followup("ambiguous_identity", candidates=infos),
            resolution_path,
        )

    chosen = candidates[0]
    if conflicting_keys(req, chosen, mapped_reg=mapped_reg):
        infos = [c.as_info() for c in candidates]
        return (
            None,
            fail_followup("ambiguous_identity", candidates=infos),
            resolution_path,
        )

    return chosen, None, resolution_path or "chat_url"

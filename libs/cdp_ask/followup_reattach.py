"""Opt-in warm-followup reattach — navigate an attached lane to a known CSE URL.

Keeps ``followup_resolve`` pure: this module owns registry side-effects and CDP
navigation when ``reattach=true`` and the CSE page is not already open.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any

from claude_bundles import cdp_registry
from claude_bundles.skills_ui_panel import connect_cdp

from cdp_ask.execution_store import LANE_HARD_LIMIT
from cdp_ask.followup_resolve import normalize_cse_url

_CSE_PATH_MARKER = "/cowork/cse_"


@dataclass(frozen=True)
class ReattachOutcome:
    """Result of ``ensure_cse_attached`` — page/pw set only on success for teardown."""

    ok: bool
    error: str | None = None
    registration_id: str | None = None
    cdp_url: str | None = None
    lane_created: bool = False
    page: Any | None = None
    pw: Any | None = None


def _lane_order(
    lanes: list[cdp_registry.Registration], purpose: str | None
) -> list[cdp_registry.Registration]:
    """Prefer lanes whose ``purpose`` matches the request when one is supplied."""
    if not purpose:
        return lanes
    matched = [lane for lane in lanes if lane.purpose == purpose]
    rest = [lane for lane in lanes if lane.purpose != purpose]
    return matched + rest


async def _verify_page_url(page: Any, chat_url: str) -> bool:
    """True when the live page URL normalizes to the requested CSE target."""
    url = page.url or ""
    if _CSE_PATH_MARKER not in url:
        return False
    return normalize_cse_url(url) == normalize_cse_url(chat_url)


async def _navigate_new_page(
    lane: cdp_registry.Registration, chat_url: str
) -> tuple[Any, Any] | None:
    """Open a fresh tab on *lane*, navigate to *chat_url*, verify URL."""
    try:
        pw, _browser, ctx, _page0 = await connect_cdp(lane.cdp_url)
    except Exception:
        return None
    page = await ctx.new_page()
    try:
        await page.goto(chat_url, wait_until="domcontentloaded")
        if not await _verify_page_url(page, chat_url):
            await page.close()
            await pw.stop()
            return None
        return page, pw
    except Exception:
        with contextlib.suppress(Exception):
            await page.close()
        await pw.stop()
        return None


async def _disconnect_playwright(pw: Any | None) -> None:
    """Stop Playwright without closing the navigated tab (retain_lane path)."""
    if pw is not None:
        with contextlib.suppress(Exception):
            await pw.stop()


async def _teardown_attempt(
    page: Any | None, pw: Any | None, *, close_page: bool = True
) -> None:
    """Close a navigated tab and disconnect Playwright without touching the lane."""
    if close_page and page is not None:
        with contextlib.suppress(Exception):
            await page.close()
    if pw is not None:
        with contextlib.suppress(Exception):
            await pw.stop()


async def ensure_cse_attached(
    chat_url: str,
    *,
    holder: str,
    purpose: str | None = None,
) -> ReattachOutcome:
    """Attach a lane and navigate to *chat_url* when the CSE is not already open."""
    lanes = list(cdp_registry.list_active())
    for lane in _lane_order(lanes, purpose):
        opened = await _navigate_new_page(lane, chat_url)
        if opened is None:
            continue
        page, pw = opened
        return ReattachOutcome(
            ok=True,
            registration_id=lane.registration_id,
            cdp_url=lane.cdp_url,
            lane_created=False,
            page=page,
            pw=pw,
        )

    if cdp_registry.count_capacity_lanes() >= LANE_HARD_LIMIT:
        return ReattachOutcome(ok=False, error="lane_capacity_exhausted")

    reg = cdp_registry.register_lane(holder=holder, purpose=purpose)
    opened = await _navigate_new_page(reg, chat_url)
    if opened is None:
        cdp_registry.deregister_lane(reg.registration_id)
        return ReattachOutcome(ok=False, error="reattach_navigate_failed")
    page, pw = opened
    return ReattachOutcome(
        ok=True,
        registration_id=reg.registration_id,
        cdp_url=reg.cdp_url,
        lane_created=True,
        page=page,
        pw=pw,
    )

"""Opt-in warm-followup reattach — navigate a registry Chrome host to a CSE URL.

Keeps ``followup_resolve`` pure: this module owns registry side-effects and CDP
navigation when ``reattach=true`` and the CSE page is not already open.

Vocabulary (arc 6885): *lane* here means a registry Chrome host (port/profile),
not an agent-bus thread. Resume = ``page.goto(chat_url)``.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any

from claude_bundles import cdp_registry
from claude_bundles.cse_url import normalize_cse_url
from claude_bundles.skills_ui_panel import connect_cdp

from cdp_ask.execution_store import ExecutionStore

_CSE_PATH_MARKER = "/cowork/cse_"


@dataclass(frozen=True)
class ReattachOutcome:
    """Result of ``ensure_cse_attached`` — page/pw set only on success for teardown.

    ``relaunched`` marks a dormant seat woken for this paste: it keeps its own
    registration and session, so teardown parks it again rather than releasing a
    minted lane.
    """

    ok: bool
    error: str | None = None
    registration_id: str | None = None
    cdp_url: str | None = None
    lane_created: bool = False
    relaunched: bool = False
    page: Any | None = None
    pw: Any | None = None


def _lane_order(
    lanes: list[cdp_registry.Registration], purpose: str | None, chat_url: str
) -> list[cdp_registry.Registration]:
    """Order live hosts: already bound to this CSE first, then purpose match.

    Navigating a host that already holds the session is a resume; navigating an
    unrelated host borrows someone else's glass, so it comes last.
    """
    target = normalize_cse_url(chat_url)
    bound: list[cdp_registry.Registration] = []
    rest: list[cdp_registry.Registration] = []
    for lane in lanes:
        current = cdp_registry.chat_url_for_registration(lane.registration_id)
        if current and normalize_cse_url(current) == target:
            bound.append(lane)
        else:
            rest.append(lane)
    if purpose:
        rest = [lane for lane in rest if lane.purpose == purpose] + [
            lane for lane in rest if lane.purpose != purpose
        ]
    return bound + rest


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


async def _wake_dormant_seat(chat_url: str, *, holder: str) -> ReattachOutcome | None:
    """Relaunch the dormant seat bound to *chat_url* and open the CSE on it."""
    seat = cdp_registry.dormant_for_chat_url(chat_url)
    if seat is None:
        return None
    try:
        reg = cdp_registry.relaunch_dormant(seat.registration_id, holder=holder)
    except Exception:
        return ReattachOutcome(ok=False, error="dormant_relaunch_failed")
    opened = await _navigate_new_page(reg, chat_url)
    if opened is None:
        with contextlib.suppress(Exception):
            cdp_registry.make_dormant(
                reg.registration_id, reason="relaunch_navigate_failed"
            )
        return ReattachOutcome(ok=False, error="reattach_navigate_failed")
    page, pw = opened
    return ReattachOutcome(
        ok=True,
        registration_id=reg.registration_id,
        cdp_url=reg.cdp_url,
        relaunched=True,
        page=page,
        pw=pw,
    )


async def ensure_cse_attached(
    chat_url: str,
    *,
    holder: str,
    purpose: str | None = None,
    allow_mint: bool = True,
    restrict_to_registration_id: str | None = None,
) -> ReattachOutcome:
    """Attach a registry Chrome host and navigate to *chat_url* when needed.

    A dormant seat bound to the URL is woken first: it owns the session's profile,
    so it resumes rather than borrowing another host's glass. With *allow_mint*
    false, an unbound URL is refused instead of minting a fresh host.
    ``restrict_to_registration_id`` limits navigation to that host after wake —
    auto-resume must not ``goto`` a lane that already holds a different CSE.
    """
    woken = await _wake_dormant_seat(chat_url, holder=holder)
    if woken is not None:
        return woken

    lanes = list(cdp_registry.list_active())
    if restrict_to_registration_id:
        lanes = [
            lane
            for lane in lanes
            if lane.registration_id == restrict_to_registration_id
        ]
    for lane in _lane_order(lanes, purpose, chat_url):
        opened = await _navigate_new_page(lane, chat_url)
        if opened is None:
            continue
        page, pw = opened
        cdp_registry.bind_session_address(lane.registration_id, chat_url=chat_url)
        return ReattachOutcome(
            ok=True,
            registration_id=lane.registration_id,
            cdp_url=lane.cdp_url,
            lane_created=False,
            page=page,
            pw=pw,
        )

    if not allow_mint:
        return ReattachOutcome(ok=False, error="reattach_no_host_available")

    reg = cdp_registry.register_lane(holder=holder, purpose=purpose)
    opened = await _navigate_new_page(reg, chat_url)
    if opened is None:
        cdp_registry.deregister_lane(reg.registration_id)
        return ReattachOutcome(ok=False, error="reattach_navigate_failed")
    page, pw = opened
    cdp_registry.bind_session_address(reg.registration_id, chat_url=chat_url)
    return ReattachOutcome(
        ok=True,
        registration_id=reg.registration_id,
        cdp_url=reg.cdp_url,
        lane_created=True,
        page=page,
        pw=pw,
    )

"""Opt-in warm-followup reattach — navigate a registry Chrome host to a CSE URL.

Keeps ``followup_resolve`` pure: this module owns registry side-effects and CDP
navigation when ``reattach=true`` and the CSE page is not already open.

Vocabulary (arc 6885): *lane* here means a registry Chrome host (port/profile),
not an agent-bus thread. Resume = ``page.goto(chat_url)``.
"""

from __future__ import annotations

import contextlib
import logging
import time
from dataclasses import dataclass, replace
from typing import Any

from claude_bundles import cdp_registry
from claude_bundles.cse_url import normalize_cse_url
from claude_bundles.skills_ui_panel import connect_cdp

logger = logging.getLogger(__name__)

_CSE_PATH_MARKER = "/cowork/cse_"
# Fixed cadence for a STATUS_TAB row that still owns the URL. No new deadline:
# the row's execution TTL is what ends the wait.
_REOPEN_INFLIGHT_POLL_S = 0.25
_PAGE_ONLY = "page_only_borrow"


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
    prior_chat_url: str | None = None
    reattach_bound_chat_url: str | None = None
    seat_snapshot_lane: str | None = None
    seat_snapshot_holder_id: str | None = None
    seat_snapshot_bound_at: float | None = None
    page_only_borrow: bool = False


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


async def find_page_on_lane(cdp_url: str, chat_url: str) -> tuple[Any, Any] | None:
    """Connect to *cdp_url* and return ``(page, playwright)`` when URL matches."""
    try:
        pw, _browser, ctx, _page0 = await connect_cdp(cdp_url)
    except Exception:
        return None
    target_norm = normalize_cse_url(chat_url)
    try:
        for page in ctx.pages:
            if normalize_cse_url(page.url or "") == target_norm:
                return page, pw
        await pw.stop()
        return None
    except Exception:
        await pw.stop()
        raise


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


def _borrow_refusal(registration_id: str, chat_url: str) -> str | None:
    """``pool_host`` or ``live_tab_same_url`` when the host must not gain *chat_url*.

    Read under the ports lock. None means the ordinary bind may write the row.
    """
    from claude_bundles.cdp_registry.tab_rows_reopen import host_accepts_borrow_bind

    store = cdp_registry._store
    with store.ports_lock():
        active = store.load_active()
        if host_accepts_borrow_bind(active, registration_id, chat_url):
            return None
        row = active.get(registration_id)
        holder = str(row.get("holder") or "") if isinstance(row, dict) else ""
        reason = (
            "pool_host"
            if isinstance(row, dict)
            and str(row.get("purpose") or "").strip() == "tab_host"
            else "live_tab_same_url"
        )
    from cdp_ask.followup_events import (
        cdp_ask_reattach_borrow_unbound,
    )
    from cdp_ask.followup_events import (
        emit as emit_followup_event,
    )

    with contextlib.suppress(Exception):
        emit_followup_event(
            cdp_ask_reattach_borrow_unbound(
                registration_id=registration_id,
                chat_url=chat_url,
                holder=holder,
                reason=reason,
            )
        )
    return _PAGE_ONLY


def _bind_chat_url(registration_id: str, chat_url: str) -> str | None:
    """Return None when *chat_url* is bound. ``attachment.conflict`` if a live holder remains.

    A pool host, or a host that already has a live tab for this URL, returns
    ``page_only_borrow`` and writes nothing. A dead ``orphaned_alive`` row that
    still claims the URL is not an attachment. Release it with the registry
    reaper and retry the bind once.
    """
    from claude_bundles import cdp_lane
    from claude_bundles.cdp_lane_reaper import release_dead_chat_url_holders
    from universal_protocol.errors import ProtocolError

    refused_borrow = _borrow_refusal(registration_id, chat_url)
    if refused_borrow:
        return refused_borrow

    def _once() -> str | None:
        if not cdp_registry.bind_chat_url_only(registration_id, chat_url=chat_url):
            return "attachment.refused"
        return None

    try:
        refused = _once()
    except ProtocolError as exc:
        if exc.code != "attachment.conflict":
            raise
    else:
        if refused:
            return refused
        return None

    store = cdp_registry._store
    with store.ports_lock():
        active = store.load_active()
        reaped = release_dead_chat_url_holders(
            active,
            cdp_lane.is_listening,
            chat_url,
        )
        if reaped:
            store.write_active(active)
    if not reaped:
        return "attachment.conflict"
    try:
        refused = _once()
    except ProtocolError as exc:
        if exc.code == "attachment.conflict":
            return "attachment.conflict"
        raise
    if refused:
        return refused
    return None


async def _bind_or_drop(
    registration_id: str,
    chat_url: str,
    page: Any,
    pw: Any,
) -> tuple[str | None, str | None, str | None, bool]:
    """Bind, or close *page* and return ``(error, prior, bound, page_only)``.

    ``page_only`` keeps the opened page without a row write. Teardown closes
    that page; this helper does not.
    """
    prior = (
        cdp_registry.chat_url_for_registration(registration_id) or ""
    ).strip() or None
    err = _bind_chat_url(registration_id, chat_url)
    if err == _PAGE_ONLY:
        return None, prior, None, True
    if err:
        await _teardown_attempt(page, pw)
        return err, prior, None, False
    return None, prior, chat_url, False


def restore_borrowed_chat_url_on_refusal(outcome: ReattachOutcome) -> None:
    """Compare-and-restore a borrow bind when followup refuses after reattach.

    A process crash between bind and teardown leaves the borrow on the registry
    row; recovery is the next bind or the reaper — not automatic on restart.
    """
    if not outcome.ok or outcome.lane_created or outcome.relaunched:
        return
    reg_id = (outcome.registration_id or "").strip()
    prior = (outcome.prior_chat_url or "").strip() or None
    bound = (outcome.reattach_bound_chat_url or "").strip() or None
    if not reg_id or not bound:
        return
    if prior and normalize_cse_url(prior) == normalize_cse_url(bound):
        return
    status = cdp_registry.restore_session_address(
        reg_id,
        expected_chat_url=bound,
        restore_to=prior,
    )
    if status == "restored":
        logger.info(
            "reattach refusal teardown: session_address_restored reg=%s restore_to=%r",
            reg_id,
            prior,
        )
    elif status == "rebound":
        logger.info(
            "reattach refusal teardown: skip chat_url restore for %s (rebound)",
            reg_id,
        )
    else:
        logger.warning(
            "reattach refusal teardown: session_address restore status=%s reg=%s",
            status,
            reg_id,
        )


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


def superseded_dormant_k44_successor_host(registration_id: str) -> str | None:
    """Return the live successor registration id for K44 page-only reach, else None.

    Predecessor must be dormant with ``seat_closed_at``; successor must hold an
    open seat on the same lane and be a host-listable registration row.
    """
    from claude_bundles import cdp_registry_store as store
    from claude_bundles.cdp_registry.models import _HOST_LISTABLE_STATUSES, seat_open

    active = store.load_active()
    row = active.get(registration_id)
    if not isinstance(row, dict) or row.get("status") != "dormant":
        return None
    if row.get("seat_closed_at") is None:
        return None
    successor_id = str(row.get("superseded_by") or "").strip()
    if not successor_id:
        return None
    lane = str(row.get("seat_lane") or row.get("parent_thread") or "").strip()
    if not lane:
        return None
    successor = active.get(successor_id)
    if not isinstance(successor, dict):
        return None
    status = str(successor.get("status") or "")
    if status == "dormant" or status not in _HOST_LISTABLE_STATUSES:
        return None
    if not seat_open(successor, lane):
        return None
    if not any(
        lane.registration_id == successor_id for lane in cdp_registry.list_active()
    ):
        return None
    return successor_id


async def _attach_k44_predecessor_page_only(
    successor_id: str,
    chat_url: str,
) -> ReattachOutcome:
    """Open *chat_url* on the successor host without registry seat/url writes."""
    lanes = [
        lane
        for lane in cdp_registry.list_active()
        if lane.registration_id == successor_id
    ]
    if len(lanes) != 1:
        return ReattachOutcome(
            ok=False,
            error="reattach_no_host_available",
            registration_id=successor_id,
        )
    lane = lanes[0]
    prior = (
        cdp_registry.chat_url_for_registration(successor_id) or ""
    ).strip() or None
    found = await find_page_on_lane(lane.cdp_url, chat_url)
    if found is not None:
        page, pw = found
    else:
        opened = await _navigate_new_page(lane, chat_url)
        if opened is None:
            return ReattachOutcome(
                ok=False,
                error="reattach_navigate_failed",
                registration_id=successor_id,
            )
        page, pw = opened
    return ReattachOutcome(
        ok=True,
        registration_id=successor_id,
        cdp_url=lane.cdp_url,
        lane_created=False,
        relaunched=False,
        page=page,
        pw=pw,
        prior_chat_url=prior,
        reattach_bound_chat_url=None,
        page_only_borrow=True,
    )


async def _wake_dormant_seat(chat_url: str, *, holder: str) -> ReattachOutcome | None:
    """Relaunch the dormant seat bound to *chat_url* and open the CSE on it."""
    seat = cdp_registry.dormant_for_chat_url(chat_url)
    if seat is None:
        return None
    try:
        reg = cdp_registry.relaunch_dormant(seat.registration_id, holder=holder)
    except Exception as exc:
        from claude_bundles import cdp_lane
        from claude_bundles import cdp_registry_store as store
        from claude_bundles.cdp_registry.tab_rows import eligible_host
        from claude_bundles.x_display_capacity import XDisplayCapacityError

        if isinstance(exc, XDisplayCapacityError):
            from claude_bundles.cdp_registry.models import normalize_account_id

            active = store.load_active()
            acct = normalize_account_id(getattr(seat, "account_id", None))
            host = eligible_host(
                active, cdp_lane.cdp_display_candidates(acct), account_id=acct
            )
            if host is not None:
                from cdp_ask.followup_events import (
                    cdp_ask_dormant_relaunch_deferred,
                )
                from cdp_ask.followup_events import (
                    emit as emit_followup_event,
                )

                with contextlib.suppress(Exception):
                    emit_followup_event(
                        cdp_ask_dormant_relaunch_deferred(
                            registration_id=seat.registration_id,
                            chat_url=chat_url,
                            display=str(host.get("display") or ""),
                            host_registration_id=str(host["registration_id"]),
                            error_code="dormant_relaunch_deferred_x_headroom",
                        )
                    )
                return ReattachOutcome(
                    ok=False,
                    error="dormant_relaunch_deferred_x_headroom",
                    registration_id=seat.registration_id,
                )
        return ReattachOutcome(
            ok=False,
            error="dormant_relaunch_failed",
            registration_id=seat.registration_id,
        )
    found = await find_page_on_lane(reg.cdp_url, chat_url)
    if found is not None:
        page, pw = found
        err, prior, bound, page_only = await _bind_or_drop(
            reg.registration_id, chat_url, page, pw
        )
        if err:
            return ReattachOutcome(ok=False, error=err, relaunched=True)
        return ReattachOutcome(
            ok=True,
            registration_id=reg.registration_id,
            cdp_url=reg.cdp_url,
            relaunched=True,
            page=page,
            pw=pw,
            prior_chat_url=prior,
            reattach_bound_chat_url=bound,
            page_only_borrow=page_only,
        )
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


def _emit_reopen_wait(
    emit_followup_event: Any,
    factory: Any,
    *,
    phase: str,
    chat_url: str,
    row: dict[str, Any],
    waited_s: float | None,
) -> None:
    entry = row.get("execution_state")
    execution_id = None
    if isinstance(entry, dict):
        execution_id = str(entry.get("execution_id") or "").strip() or None
    registration_id = str(row.get("registration_id") or "").strip() or None
    with contextlib.suppress(Exception):
        emit_followup_event(
            factory(
                phase=phase,
                chat_url=chat_url,
                registration_id=registration_id,
                execution_id=execution_id,
                waited_s=waited_s,
            )
        )


async def ensure_cse_attached(
    chat_url: str,
    *,
    holder: str,
    purpose: str | None = None,
    parent_thread: str | None = None,
    mission_kind: str | None = None,
    allow_mint: bool = True,
    restrict_to_registration_id: str | None = None,
) -> ReattachOutcome:
    """Attach a registry Chrome host and navigate to *chat_url* when needed.

    A dormant seat bound to the URL is woken first: it owns the session's profile,
    so it resumes rather than borrowing another host's glass. With *allow_mint*
    false, an unbound URL is refused instead of minting a fresh host.
    ``restrict_to_registration_id`` limits navigation to that host after wake —
    auto-resume must not ``goto`` a lane that already holds a different CSE.

    Chat URL is bound without seating. The lane seat snapshot is taken before
    navigation so a later commit can refuse if that holder moved.
    """
    from claude_bundles.cdp_registry.session_address import open_lane_seat_holder

    lane_key = (parent_thread or "").strip()
    snap_holder, snap_bound = (
        open_lane_seat_holder(lane_key) if lane_key else (None, None)
    )

    import asyncio

    from claude_bundles.cdp_registry.tab_rows import inflight_row_for_chat_url

    from cdp_ask.followup_events import (
        cdp_ask_reopen_inflight_wait,
    )
    from cdp_ask.followup_events import (
        emit as emit_followup_event,
    )

    waited_from: float | None = None
    waiting_on: dict[str, Any] | None = None
    while True:
        inflight = await asyncio.to_thread(inflight_row_for_chat_url, chat_url)
        if inflight is None or str(inflight.get("status") or "") != "tab":
            if waiting_on is not None:
                _emit_reopen_wait(
                    emit_followup_event,
                    cdp_ask_reopen_inflight_wait,
                    phase="cleared",
                    chat_url=chat_url,
                    row=waiting_on,
                    waited_s=time.monotonic() - (waited_from or time.monotonic()),
                )
            break
        if waiting_on is None:
            waiting_on = inflight
            waited_from = time.monotonic()
            _emit_reopen_wait(
                emit_followup_event,
                cdp_ask_reopen_inflight_wait,
                phase="started",
                chat_url=chat_url,
                row=inflight,
                waited_s=None,
            )
        await asyncio.sleep(_REOPEN_INFLIGHT_POLL_S)

    def _done(
        outcome: ReattachOutcome, *, refresh_seat: bool = False
    ) -> ReattachOutcome:
        if not outcome.ok:
            return outcome
        holder_id, bound_at = snap_holder, snap_bound
        # Dormant relaunch calls bind_driving_seat and advances seat_bound_at.
        # A snapshot taken before that wake fails the later commit CAS.
        if refresh_seat and lane_key:
            holder_id, bound_at = open_lane_seat_holder(lane_key)
        return replace(
            outcome,
            seat_snapshot_lane=lane_key or None,
            seat_snapshot_holder_id=holder_id,
            seat_snapshot_bound_at=bound_at,
        )

    dormant = cdp_registry.dormant_for_chat_url(chat_url)
    if dormant is not None:
        k44_successor = superseded_dormant_k44_successor_host(dormant.registration_id)
        if k44_successor:
            if (
                restrict_to_registration_id
                and restrict_to_registration_id != k44_successor
            ):
                return ReattachOutcome(
                    ok=False,
                    error="reattach_no_host_available",
                    registration_id=k44_successor,
                )
            k44_outcome = await _attach_k44_predecessor_page_only(
                k44_successor, chat_url
            )
            return _done(k44_outcome, refresh_seat=False)

    woken = await _wake_dormant_seat(chat_url, holder=holder)
    if woken is not None:
        return _done(woken, refresh_seat=True)

    lanes = list(cdp_registry.list_active())
    if restrict_to_registration_id:
        lanes = [
            lane
            for lane in lanes
            if lane.registration_id == restrict_to_registration_id
        ]
    for lane in _lane_order(lanes, purpose, chat_url):
        found = await find_page_on_lane(lane.cdp_url, chat_url)
        if found is not None:
            page, pw = found
            err, prior, bound, page_only = await _bind_or_drop(
                lane.registration_id, chat_url, page, pw
            )
            if err:
                return ReattachOutcome(ok=False, error=err)
            return _done(
                ReattachOutcome(
                    ok=True,
                    registration_id=lane.registration_id,
                    cdp_url=lane.cdp_url,
                    lane_created=False,
                    page=page,
                    pw=pw,
                    prior_chat_url=prior,
                    reattach_bound_chat_url=bound,
                    page_only_borrow=page_only,
                )
            )
        opened = await _navigate_new_page(lane, chat_url)
        if opened is None:
            continue
        page, pw = opened
        err, prior, bound, page_only = await _bind_or_drop(
            lane.registration_id, chat_url, page, pw
        )
        if err:
            return ReattachOutcome(ok=False, error=err)
        return _done(
            ReattachOutcome(
                ok=True,
                registration_id=lane.registration_id,
                cdp_url=lane.cdp_url,
                lane_created=False,
                page=page,
                pw=pw,
                prior_chat_url=prior,
                reattach_bound_chat_url=bound,
                page_only_borrow=page_only,
            )
        )

    if not allow_mint:
        return ReattachOutcome(ok=False, error="reattach_no_host_available")

    reg = cdp_registry.register_lane(
        holder=holder,
        purpose=purpose,
        parent_thread=parent_thread,
        mission_kind=mission_kind,
    )
    opened = await _navigate_new_page(reg, chat_url)
    if opened is None:
        cdp_registry.deregister_lane(reg.registration_id)
        return ReattachOutcome(ok=False, error="reattach_navigate_failed")
    page, pw = opened
    err, prior, bound, page_only = await _bind_or_drop(
        reg.registration_id, chat_url, page, pw
    )
    if err:
        cdp_registry.deregister_lane(reg.registration_id)
        return ReattachOutcome(ok=False, error=err)
    return _done(
        ReattachOutcome(
            ok=True,
            registration_id=reg.registration_id,
            cdp_url=reg.cdp_url,
            lane_created=True,
            page=page,
            pw=pw,
            prior_chat_url=prior,
            reattach_bound_chat_url=bound,
            page_only_borrow=page_only,
        )
    )

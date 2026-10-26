"""Tab-row resume attach — reopen the row's own target, never borrow a host URL.

``resume_execution`` calls ``attach_reopened_tab`` when the registry row is
``STATUS_TAB``. Host choice is the row's own host while it is still eligible,
otherwise ``eligible_host``, otherwise one pool-host mint under
``tab_host_mint_lock``. The Playwright page is the CDP target id on the row,
not the first page that shows the URL. Failures return the row to address-only
with ``tab_kept_reason=reopen_failed``. ``XDisplayCapacityError`` propagates
so the caller records ``attach_raised``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from claude_bundles import cdp_lane
from claude_bundles import cdp_registry_store as _store
from claude_bundles.cdp_registry.models import normalize_account_id
from claude_bundles.cdp_registry.tab_rows import eligible_host, tab_host_mint_lock
from claude_bundles.cdp_registry.tab_rows_reopen import (
    host_row_eligible,
    mark_tab_reopen_failed,
    reopen_tab_row,
)
from claude_bundles.x_display_capacity import XDisplayCapacityError

from cdp_ask.followup_reattach import ReattachOutcome, _teardown_attempt

logger = logging.getLogger(__name__)


def resolve_resume_host(row: dict[str, Any], *, trigger: str) -> dict[str, Any]:
    """Pick the Chrome that should show *row* again, minting one pool host if needed.

    The mint re-reads eligibility under ``tab_host_mint_lock`` so concurrent
    resumes share a single ``register_lane(purpose="tab_host")``.
    """
    active = _store.load_active()
    own_id = str(row.get("host_registration_id") or "").strip()
    own = active.get(own_id) if own_id else None
    if isinstance(own, dict) and host_row_eligible(own):
        return own
    acct = normalize_account_id(row.get("account_id"))
    host = eligible_host(
        active, cdp_lane.cdp_display_candidates(acct), account_id=acct
    )
    if host is not None:
        return host
    return _mint_pool_host(trigger=trigger, account_id=acct)


def _mint_pool_host(*, trigger: str, account_id: str | None = None) -> dict[str, Any]:
    from claude_bundles import cdp_registry_events as _events
    from claude_bundles.cdp_registry.driver_locks import _release_driver_lock
    from claude_bundles.cdp_registry.lifecycle import register_lane
    from claude_bundles.cdp_registry.models import RegistryError

    minted_trigger = "resume" if trigger == "host_lost" else trigger
    if minted_trigger not in {"ask", "resume"}:
        minted_trigger = "resume"
    with tab_host_mint_lock():
        active = _store.load_active()
        acct = normalize_account_id(account_id)
        host = eligible_host(
            active, cdp_lane.cdp_display_candidates(acct), account_id=acct
        )
        if host is not None:
            return host
        host_reg = register_lane(
            holder="cdp-tab-pool",
            purpose="tab_host",
            mission_kind=None,
            parent_thread=None,
            account_id=acct if acct != "default" else None,
        )
        _release_driver_lock(host_reg.registration_id)
        active = _store.load_active()
        minted = active.get(host_reg.registration_id)
        with contextlib.suppress(Exception):
            _events.emit(
                _events.cdp_tab_host_minted(
                    registration_id=host_reg.registration_id,
                    display=str(host_reg.display or ""),
                    port=host_reg.port,
                    trigger=minted_trigger,
                )
            )
        if not isinstance(minted, dict):
            raise RegistryError("pool host mint did not persist")
        return minted


async def _page_target_id(page: Any) -> str:
    direct = getattr(page, "target_id", None)
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    context = getattr(page, "context", None)
    opener = getattr(context, "new_cdp_session", None)
    if opener is None:
        return ""
    session = await opener(page)
    try:
        info = await session.send("Target.getTargetInfo")
    finally:
        detach = getattr(session, "detach", None)
        if detach is not None:
            with contextlib.suppress(Exception):
                await detach()
    if not isinstance(info, dict):
        return ""
    target = info.get("targetInfo") or {}
    return str(target.get("targetId") or "").strip()


async def _select_target_page(cdp_url: str, target_id: str) -> tuple[Any, Any] | None:
    from claude_bundles.skills_ui_panel import connect_cdp

    pw, _browser, ctx, _page0 = await connect_cdp(cdp_url)
    try:
        for page in ctx.pages:
            if await _page_target_id(page) == target_id:
                return page, pw
    except Exception:
        await pw.stop()
        raise
    await pw.stop()
    return None


async def attach_reopened_tab(
    record: Any, row: dict[str, Any], *, trigger: str
) -> ReattachOutcome:
    """Reopen *row* and return the page whose CDP target id the row records.

    ``registration_id`` on the outcome is the tab row, so resume does not
    transfer the execution onto the host. A session-id mismatch is a failure.
    """
    from cdp_ask.execution_resume import _cse_id

    rid = str(record.registration_id or "")
    chat_url = str(row.get("chat_url") or "")
    try:
        host = await asyncio.to_thread(resolve_resume_host, row, trigger=trigger)
        await asyncio.to_thread(
            reopen_tab_row,
            rid,
            str(host["registration_id"]),
            trigger,
        )
    except XDisplayCapacityError:
        await asyncio.to_thread(mark_tab_reopen_failed, rid)
        raise
    except Exception:
        logger.warning("tab resume %s reopen failed", rid, exc_info=True)
        await asyncio.to_thread(mark_tab_reopen_failed, rid)
        return ReattachOutcome(ok=False, error="tab_reopen_failed", registration_id=rid)

    fresh = await asyncio.to_thread(_store.load_active)
    current = fresh.get(rid) if isinstance(fresh, dict) else None
    target_id = str((current or {}).get("target_id") or "").strip()
    port = (current or {}).get("port")
    if not target_id or not isinstance(port, int):
        await asyncio.to_thread(mark_tab_reopen_failed, rid)
        return ReattachOutcome(ok=False, error="tab_reopen_failed", registration_id=rid)
    cdp_url = f"http://127.0.0.1:{port}"
    try:
        selected = await _select_target_page(cdp_url, target_id)
    except Exception:
        logger.warning("tab resume %s page select failed", rid, exc_info=True)
        await asyncio.to_thread(mark_tab_reopen_failed, rid)
        return ReattachOutcome(ok=False, error="tab_reopen_failed", registration_id=rid)
    if selected is None:
        await asyncio.to_thread(mark_tab_reopen_failed, rid)
        return ReattachOutcome(ok=False, error="tab_reopen_failed", registration_id=rid)
    page, pw = selected
    if _cse_id(str(getattr(page, "url", "") or "")) != _cse_id(chat_url):
        await _teardown_attempt(page, pw, close_page=True)
        await asyncio.to_thread(mark_tab_reopen_failed, rid)
        return ReattachOutcome(ok=False, error="tab_reopen_failed", registration_id=rid)
    return ReattachOutcome(
        ok=True,
        registration_id=rid,
        cdp_url=cdp_url,
        page=page,
        pw=pw,
    )

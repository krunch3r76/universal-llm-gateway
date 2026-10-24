"""Reopen, release, and borrow predicates for disposable STATUS_TAB rows.

``cdp_registry.tab_rows`` keeps host selection and fresh asks. This module
owns the reopen transition, the address-only keep reasons, and the predicate
that refuses writing a borrowed ``chat_url`` onto a pool host. Callers are
``execution_resume`` and ``followup_reattach``. Authority stays ``active.json``
under the ports lock; events here are advisory.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Callable
from typing import Any

from claude_bundles import cdp_lane
from claude_bundles import cdp_registry_events as _events
from claude_bundles import cdp_registry_store as _store

from .models import STATUS_TAB, Registration, RegistryError


def host_row_eligible(
    row: dict[str, Any],
    *,
    listen: Callable[[int], bool] | None = None,
) -> bool:
    """True when *row* can host a resumed tab on a candidate display.

    Eligibility is the status-shape rule: capacity status, a live port inside
    the registry range, not a pin and not 9222, and a display
    ``cdp_display_candidates`` would mint on.
    """
    from .tab_rows import _host_capacity_eligible_row

    is_up = listen or cdp_lane.is_listening
    display = str(row.get("display") or "").strip()
    if not display:
        return False
    candidates = {
        cdp_lane._display_key(item) for item in cdp_lane.cdp_display_candidates()
    }
    if cdp_lane._display_key(display) not in candidates:
        return False
    return _host_capacity_eligible_row(row, display=display, listen=is_up)


def host_accepts_borrow_bind(
    active: dict[str, Any] | Any, host_id: str, chat_url: str
) -> bool:
    """False when *host_id* is a pool host or already has a live tab for *chat_url*.

    A missing host row accepts the bind: the caller is minting, not borrowing
    a shared Chrome. Read *active* under the ports lock; this function does
    not take it.
    """
    from claude_bundles.cse_url import normalize_cse_url

    from .tab_rows import tab_rows_for_host

    row = active.get(host_id)
    if not isinstance(row, dict):
        return True
    if str(row.get("purpose") or "").strip() == "tab_host":
        return False
    target = normalize_cse_url(chat_url or "")
    for tab in tab_rows_for_host(active, host_id):
        tid = str(tab.get("target_id") or "").strip()
        if not tid:
            continue
        if normalize_cse_url(str(tab.get("chat_url") or "")) == target:
            return False
    return True


def _close_target_on_port(port: int, target_id: str) -> None:
    cdp_lane.close_cdp_target(port, target_id)


def release_tab_row(registration_id: str) -> None:
    """Close the tab target and delete the row. A tab never becomes ``released``."""
    rid = str(registration_id or "").strip()
    if not rid:
        return
    port: int | None = None
    target_id = ""
    chat_url = ""
    row_copy: dict[str, Any] | None = None
    with _store.ports_lock():
        active = _store.load_active()
        row = active.get(rid)
        if not isinstance(row, dict) or row.get("status") != STATUS_TAB:
            return
        row_copy = dict(row)
        port = row.get("port") if isinstance(row.get("port"), int) else None
        target_id = str(row.get("target_id") or "").strip()
        chat_url = str(row.get("chat_url") or "").strip()
        active.pop(rid, None)
        _store.write_active(active)
    if isinstance(port, int) and target_id:
        with contextlib.suppress(Exception):
            _close_target_on_port(port, target_id)
    if row_copy is None:
        return
    _store.append_log("tab_released", {"registration_id": rid, **row_copy})
    with contextlib.suppress(Exception):
        _events.emit(
            _events.cdp_tab_closed(
                registration_id=rid,
                host_registration_id=str(row_copy.get("host_registration_id") or ""),
                reason="exit",
            )
        )
    if chat_url and isinstance(port, int):
        from claude_bundles.cse_provenance import append_episode

        with contextlib.suppress(Exception):
            append_episode(
                chat_url=chat_url,
                registration_id=rid,
                cdp_url=f"http://127.0.0.1:{port}",
                state="tab_released",
            )


def finish_tab_on_exit(reg: Registration, *, branch: str) -> None:
    """Apply the tab exit branch: release, or keep the row address-only.

    ``normal`` deletes the row. ``wake_debt`` keeps the target open.
    ``abort`` and every other kept branch close the target and record
    *branch* as ``tab_kept_reason`` (address-only row).
    """
    from claude_bundles.cse_wake_retain import registration_has_wake_debt

    rid = reg.registration_id
    if branch == "normal":
        release_tab_row(rid)
        return
    if branch == "wake_debt" or registration_has_wake_debt(rid):
        _keep_tab_row(rid, reason="wake_debt", close_target=False)
        return
    if branch == "abort":
        _keep_tab_row(rid, reason="abort_unattested", close_target=True)
        return
    _keep_tab_row(
        rid,
        reason="retain_host" if branch == "retain_host" else branch,
        close_target=True,
    )


def _keep_tab_row(registration_id: str, *, reason: str, close_target: bool) -> None:
    rid = str(registration_id or "").strip()
    port: int | None = None
    target_id = ""
    host_id = ""
    with _store.ports_lock():
        active = _store.load_active()
        row = active.get(rid)
        if not isinstance(row, dict) or row.get("status") != STATUS_TAB:
            return
        port = row.get("port") if isinstance(row.get("port"), int) else None
        target_id = str(row.get("target_id") or "").strip()
        host_id = str(row.get("host_registration_id") or "")
        updated = dict(row)
        updated["target_id"] = None
        updated["tab_kept_reason"] = reason
        active[rid] = updated
        _store.write_active(active)
    if close_target and isinstance(port, int) and target_id:
        with contextlib.suppress(Exception):
            _close_target_on_port(port, target_id)
    with contextlib.suppress(Exception):
        _events.emit(
            _events.cdp_tab_kept(
                registration_id=rid,
                host_registration_id=host_id,
                reason=reason,
                target_closed=close_target,
            )
        )


def mark_tab_reopen_failed(registration_id: str) -> None:
    """Return *registration_id* to address-only with ``tab_kept_reason=reopen_failed``.

    Closes a target the failed reopen managed to record. A missing row is a
    no-op so a later release cannot be undone.
    """
    _keep_tab_row(registration_id, reason="reopen_failed", close_target=True)


def reopen_tab_row(
    registration_id: str,
    host_registration_id: str,
    trigger: str = "boot",
) -> None:
    """Point a tab row at *host_registration_id* and open a fresh CDP target.

    The row is rewritten under the ports lock before the target opens: host,
    port, and ``holder_pid`` move, ``tab_kept_reason`` clears, and
    ``target_id`` stays empty until the new target id is written. A stale
    target is closed on the row's previous port when that port still listens.
    ``chat_url`` is not rewritten. Emits ``cdp_tab_reopened``.
    """
    rid = str(registration_id or "").strip()
    hid = str(host_registration_id or "").strip()
    with _store.ports_lock():
        active = _store.load_active()
        row = active.get(rid)
        host = active.get(hid)
        if not isinstance(row, dict) or row.get("status") != STATUS_TAB:
            raise RegistryError(f"tab row {rid!r} missing")
        if not isinstance(host, dict):
            raise RegistryError(f"host {hid!r} missing")
        port = int(host["port"])
        stale = str(row.get("target_id") or "").strip()
        old_port = row.get("port") if isinstance(row.get("port"), int) else None
        prior_host = str(row.get("host_registration_id") or "").strip()
        updated = dict(row)
        updated["host_registration_id"] = hid
        updated["port"] = port
        updated["holder_pid"] = os.getpid()
        updated["tab_kept_reason"] = None
        updated["target_id"] = None
        active[rid] = updated
        _store.write_active(active)
    stale_closed = False
    if stale and isinstance(old_port, int) and cdp_lane.is_listening(old_port):
        with contextlib.suppress(Exception):
            _close_target_on_port(old_port, stale)
            stale_closed = True
    url = str(row.get("chat_url") or "").strip()
    target_id: str | None = None
    try:
        target_id = cdp_lane.open_cdp_tab_target(port, url or "about:blank")
        with _store.ports_lock():
            active = _store.load_active()
            current = active.get(rid)
            if isinstance(current, dict):
                active[rid] = {**current, "target_id": target_id}
                _store.write_active(active)
    except Exception:
        if target_id:
            with contextlib.suppress(Exception):
                _close_target_on_port(port, target_id)
        raise
    with contextlib.suppress(Exception):
        _events.emit(
            _events.cdp_tab_reopened(
                registration_id=rid,
                host_registration_id=hid,
                prior_host_registration_id=prior_host or None,
                trigger=trigger if trigger in {"boot", "host_lost"} else "boot",
                stale_target_closed=stale_closed,
            )
        )


async def select_page_by_target_id(ctx: Any, target_id: str | None) -> Any:
    """Return the Playwright page whose CDP target id matches *target_id*."""
    tid = str(target_id or "").strip()
    if not tid:
        raise RegistryError("target_id required")
    for _ in range(8):
        for page in list(getattr(ctx, "pages", []) or []):
            direct = getattr(page, "target_id", None)
            resolved = str(direct or "").strip()
            if not resolved:
                new_session = getattr(ctx, "new_cdp_session", None)
                if new_session is None:
                    page_ctx = getattr(page, "context", None)
                    new_session = getattr(page_ctx, "new_cdp_session", None)
                if new_session is not None:
                    session = await new_session(page)
                    try:
                        info = await session.send("Target.getTargetInfo")
                        resolved = str(
                            (info.get("targetInfo") or {}).get("targetId") or ""
                        ).strip()
                    finally:
                        await session.detach()
            if resolved == tid:
                return page
        await asyncio.sleep(0.1)
    raise RegistryError(f"CDP target {tid!r} not found in browser context")

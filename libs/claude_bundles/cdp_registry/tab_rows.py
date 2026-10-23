"""Elastic tab pool — disposable views on shared CDP host Chromes."""

from __future__ import annotations

import contextlib
import fcntl
import os
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

from claude_bundles import cdp_lane
from claude_bundles import cdp_registry_events as _events
from claude_bundles import cdp_registry_store as _store

from .driver_locks import _release_driver_lock
from .models import (
    STATUS_TAB,
    Registration,
    RegistryError,
    _CAPACITY_STATUSES,
    _row_to_registration,
)
from .ports import PORT_RANGE, _pin_port_block

_TAB_MINT_LOCK = Path.home() / ".gateway" / "cdp-registry" / "tab_host_mint.lock"


def _host_eligible_row(
    row: dict[str, Any],
    *,
    display: str,
    listen: Callable[[int], bool],
) -> bool:
    if row.get("status") not in _CAPACITY_STATUSES:
        return False
    port = row.get("port")
    if not isinstance(port, int):
        return False
    if port == 9222 or port not in PORT_RANGE or port in _pin_port_block():
        return False
    row_display = str(row.get("display") or "").strip()
    if cdp_lane._display_key(row_display) != cdp_lane._display_key(display):
        return False
    return listen(port)


def eligible_host(
    active: Mapping[str, Any],
    candidates: list[str],
    *,
    listen: Callable[[int], bool] | None = None,
) -> dict[str, Any] | None:
    """First eligible host on candidate displays, in candidate order."""
    is_up = listen or cdp_lane.is_listening
    for display in candidates:
        for row in active.values():
            if not isinstance(row, dict):
                continue
            rid = str(row.get("registration_id") or "").strip()
            if not rid:
                continue
            if _host_eligible_row(row, display=display, listen=is_up):
                return row
    return None


def tab_rows_for_host(
    active: Mapping[str, Any], host_registration_id: str
) -> list[dict[str, Any]]:
    hid = str(host_registration_id or "").strip()
    out: list[dict[str, Any]] = []
    for row in active.values():
        if not isinstance(row, dict) or row.get("status") != STATUS_TAB:
            continue
        if str(row.get("host_registration_id") or "").strip() == hid:
            out.append(row)
    return out


def live_tab_rows_for_host(
    active: Mapping[str, Any], host_registration_id: str
) -> list[dict[str, Any]]:
    return [
        row
        for row in tab_rows_for_host(active, host_registration_id)
        if str(row.get("target_id") or "").strip()
    ]


def inflight_row_for_chat_url(chat_url: str) -> dict[str, Any] | None:
    from claude_bundles.cse_url import normalize_cse_url
    from claude_bundles.cdp_registry.execution_state import row_execution_in_flight

    target = normalize_cse_url(chat_url or "")
    if not target:
        return None
    from .session_address import attachment_for_chat_url

    host_row = attachment_for_chat_url(chat_url)
    if host_row is not None:
        active = _store.load_active()
        row = active.get(host_row.registration_id)
        if isinstance(row, dict) and row_execution_in_flight(row):
            return row
    active = _store.load_active()
    for row in active.values():
        if not isinstance(row, dict) or row.get("status") != STATUS_TAB:
            continue
        if normalize_cse_url(str(row.get("chat_url") or "")) != target:
            continue
        if row_execution_in_flight(row):
            return row
    return None


def host_accepts_borrow_bind(
    active: Mapping[str, Any], host_id: str, chat_url: str
) -> bool:
    from claude_bundles.cse_url import normalize_cse_url

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


@contextlib.contextmanager
def tab_host_mint_lock() -> Iterator[None]:
    """Serialize pool-host mint across concurrent tab asks."""
    _TAB_MINT_LOCK.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(_TAB_MINT_LOCK), os.O_CREAT | os.O_RDWR, 0o600)
    started_wait = False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            started_wait = True
            with contextlib.suppress(Exception):
                _events.emit(
                    _events.cdp_tab_host_mint_lock_wait(phase="started")
                )
            fcntl.flock(fd, fcntl.LOCK_EX)
        with contextlib.suppress(Exception):
            _events.emit(
                _events.cdp_tab_host_mint_lock_wait(
                    phase="acquired",
                    outcome="minting" if started_wait else "joined_existing",
                )
            )
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _close_target_on_port(port: int, target_id: str) -> None:
    cdp_lane.close_cdp_target(port, target_id)


def release_tab_row(registration_id: str) -> None:
    rid = str(registration_id or "").strip()
    if not rid:
        return
    with _store.ports_lock():
        active = _store.load_active()
        row = active.get(rid)
        if not isinstance(row, dict) or row.get("status") != STATUS_TAB:
            return
        port = row.get("port")
        target_id = str(row.get("target_id") or "").strip()
        if isinstance(port, int) and target_id:
            with contextlib.suppress(Exception):
                _close_target_on_port(port, target_id)
        chat_url = str(row.get("chat_url") or "").strip()
        active.pop(rid, None)
        _store.write_active(active)
        _store.append_log("tab_released", {"registration_id": rid, **row})
        with contextlib.suppress(Exception):
            _events.emit(
                _events.cdp_tab_closed(
                    registration_id=rid,
                    host_registration_id=str(row.get("host_registration_id") or ""),
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


def set_tab_chat_url(
    registration_id: str,
    *,
    chat_url: str,
    execution_id: str | None = None,
) -> None:
    rid = str(registration_id or "").strip()
    url = str(chat_url or "").strip()
    if not rid or not url:
        return
    with _store.ports_lock():
        active = _store.load_active()
        row = active.get(rid)
        if not isinstance(row, dict) or row.get("status") != STATUS_TAB:
            return
        updated = dict(row)
        updated["chat_url"] = url
        if execution_id:
            updated["execution_id"] = execution_id
        active[rid] = updated
        _store.write_active(active)


def finish_tab_on_exit(reg: Registration, *, branch: str) -> None:
    from claude_bundles.cse_wake_retain import registration_has_wake_debt

    rid = reg.registration_id
    if branch == "normal":
        release_tab_row(rid)
        return
    if branch == "wake_debt" or registration_has_wake_debt(rid):
        _keep_tab_row(rid, reason="wake_debt", close_target=False)
        return
    if branch == "abort":
        _keep_tab_row(rid, reason="abort_unattested", close_target=False)
        return
    # retain_host, teardown, host_lost escape
    _keep_tab_row(
        rid,
        reason="retain_host" if branch == "retain_host" else branch,
        close_target=True,
    )


def _keep_tab_row(
    registration_id: str, *, reason: str, close_target: bool
) -> None:
    rid = str(registration_id or "").strip()
    with _store.ports_lock():
        active = _store.load_active()
        row = active.get(rid)
        if not isinstance(row, dict) or row.get("status") != STATUS_TAB:
            return
        port = row.get("port")
        target_id = str(row.get("target_id") or "").strip()
        if close_target and isinstance(port, int) and target_id:
            with contextlib.suppress(Exception):
                _close_target_on_port(port, target_id)
        updated = dict(row)
        updated["target_id"] = None
        updated["tab_kept_reason"] = reason
        active[rid] = updated
        _store.write_active(active)
        with contextlib.suppress(Exception):
            _events.emit(
                _events.cdp_tab_kept(
                    registration_id=rid,
                    host_registration_id=str(row.get("host_registration_id") or ""),
                    reason=reason,
                    target_closed=close_target,
                )
            )


def sweep_kept_tab_rows() -> list[str]:
    from claude_bundles.cse_wake_retain import registration_has_wake_debt
    from claude_bundles.cdp_registry.execution_state import row_execution_in_flight

    released: list[str] = []
    active = _store.load_active()
    listen = cdp_lane.is_listening
    for rid, row in list(active.items()):
        if not isinstance(row, dict) or row.get("status") != STATUS_TAB:
            continue
        if row_execution_in_flight(row):
            continue
        if registration_has_wake_debt(str(rid)):
            continue
        host_id = str(row.get("host_registration_id") or "").strip()
        host = active.get(host_id) if host_id else None
        host_dead = not isinstance(host, dict) or host.get("status") not in _CAPACITY_STATUSES
        port = row.get("port")
        port_dead = isinstance(port, int) and not listen(port)
        if row.get("tab_kept_reason") or host_dead or port_dead:
            release_tab_row(str(rid))
            released.append(str(rid))
    return released


def reopen_tab_row(registration_id: str, host_registration_id: str) -> None:
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
        stale_closed = False
        if stale and cdp_lane.is_listening(port):
            with contextlib.suppress(Exception):
                _close_target_on_port(port, stale)
                stale_closed = True
        prior_host = str(row.get("host_registration_id") or "").strip()
        updated = dict(row)
        updated["host_registration_id"] = hid
        updated["port"] = port
        updated["holder_pid"] = os.getpid()
        updated["tab_kept_reason"] = None
        updated["target_id"] = None
        active[rid] = updated
        _store.write_active(active)
    url = str(row.get("chat_url") or "").strip()
    target_id = cdp_lane.open_cdp_tab_target(port, url or "about:blank")
    with _store.ports_lock():
        active = _store.load_active()
        current = active.get(rid)
        if isinstance(current, dict):
            active[rid] = {**current, "target_id": target_id}
            _store.write_active(active)
    with contextlib.suppress(Exception):
        _events.emit(
            _events.cdp_tab_reopened(
                registration_id=rid,
                host_registration_id=hid,
                prior_host_registration_id=prior_host or None,
                trigger="boot",
                stale_target_closed=stale_closed,
            )
        )


def open_tab_row_for_ask(
    *,
    holder: str,
    purpose: str | None,
    mission_kind: str | None,
    parent_thread: str | None,
    host_row: dict[str, Any] | None = None,
    trigger: str = "ask",
) -> Registration:
    """Mint or reuse a pool host, write a tab row, open the CDP target."""
    from .lifecycle import register_lane

    listen = cdp_lane.is_listening
    with tab_host_mint_lock():
        active = _store.load_active()
        candidates = cdp_lane.cdp_display_candidates()
        host = host_row or eligible_host(active, candidates, listen=listen)
        minted = False
        if host is None:
            host_reg = register_lane(
                holder="cdp-tab-pool",
                purpose="tab_host",
                mission_kind=None,
                parent_thread=None,
            )
            minted = True
            _release_driver_lock(host_reg.registration_id)
            active = _store.load_active()
            host = active.get(host_reg.registration_id)
            with contextlib.suppress(Exception):
                _events.emit(
                    _events.cdp_tab_host_minted(
                        registration_id=host_reg.registration_id,
                        display=str(host_reg.display or ""),
                        port=host_reg.port,
                        trigger=trigger,
                    )
                )
        if not isinstance(host, dict):
            raise RegistryError("no eligible host for tab ask")
        host_id = str(host["registration_id"])
        port = int(host["port"])
        tab_id = uuid.uuid4().hex
        with _store.ports_lock():
            active = _store.load_active()
            tab_row: dict[str, Any] = {
                "registration_id": tab_id,
                "status": STATUS_TAB,
                "host_registration_id": host_id,
                "port": port,
                "holder": holder,
                "purpose": purpose,
                "mission_kind": mission_kind,
                "parent_thread": parent_thread,
                "display": host.get("display"),
                "profile_suffix": host.get("profile_suffix"),
                "holder_pid": os.getpid(),
                "started_at": time.time(),
                "target_id": None,
            }
            active[tab_id] = tab_row
            _store.write_active(active)
            _store.append_log("tab_opened", tab_row)
        target_id = cdp_lane.open_cdp_tab_target(port, "about:blank")
        with _store.ports_lock():
            active = _store.load_active()
            row = active.get(tab_id)
            if isinstance(row, dict):
                active[tab_id] = {**row, "target_id": target_id}
                _store.write_active(active)
        with contextlib.suppress(Exception):
            _events.emit(
                _events.cdp_tab_opened(
                    registration_id=tab_id,
                    host_registration_id=host_id,
                    port=port,
                    minted_host=minted,
                )
            )
        return _row_to_registration(active[tab_id])

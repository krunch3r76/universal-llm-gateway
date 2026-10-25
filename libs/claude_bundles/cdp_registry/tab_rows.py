"""Elastic tab pool rows are disposable views on shared CDP host Chromes, not capacity hosts."""

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
    _CAPACITY_STATUSES,
    STATUS_TAB,
    Registration,
    RegistryError,
    _row_to_registration,
    normalize_account_id,
)
from .ports import PORT_RANGE, _pin_port_block
from .tab_rows_reopen import (  # noqa: F401 — re-export; reopen lives beside this module
    finish_tab_on_exit,
    host_accepts_borrow_bind,
    release_tab_row,
    reopen_tab_row,
    select_page_by_target_id,
)

_TAB_MINT_LOCK = Path.home() / ".gateway" / "cdp-registry" / "tab_host_mint.lock"


def _host_capacity_eligible_row(
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


def _host_eligible_row(
    row: dict[str, Any],
    *,
    display: str,
    listen: Callable[[int], bool],
) -> bool:
    from claude_bundles.what_is_running_view import OPERATOR_PURPOSES

    from .models import seat_open

    purpose = str(row.get("purpose") or "").strip().lower()
    if purpose in OPERATOR_PURPOSES or seat_open(row):
        return False
    return _host_capacity_eligible_row(row, display=display, listen=listen)


def eligible_host(
    active: Mapping[str, Any],
    candidates: list[str],
    *,
    listen: Callable[[int], bool] | None = None,
    account_id: str | None = None,
) -> dict[str, Any] | None:
    """Pick an eligible tab_host row, optionally filtered by account."""
    is_up = listen or cdp_lane.is_listening
    want_account = normalize_account_id(account_id)
    matches: list[tuple[int, dict[str, Any]]] = []
    for display_index, display in enumerate(candidates):
        for row in active.values():
            if not isinstance(row, dict):
                continue
            rid = str(row.get("registration_id") or "").strip()
            if not rid:
                continue
            if not _host_eligible_row(row, display=display, listen=is_up):
                continue
            row_account = normalize_account_id(row.get("account_id"))
            if row_account != want_account:
                continue
            matches.append((display_index, row))
    if not matches:
        return None
    if len(matches) > 1:
        _display_index, row = min(
            matches,
            key=lambda item: (
                len(live_tab_rows_for_host(active, str(item[1]["registration_id"]))),
                item[0],
            ),
        )
        return row
    return matches[0][1]


def tab_rows_for_host(
    active: Mapping[str, Any], host_registration_id: str
) -> list[dict[str, Any]]:
    """Return every STATUS_TAB row whose host registration id matches the given host."""
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
    """Return tab rows on this host that still hold a live target or are opening."""
    return [
        row
        for row in tab_rows_for_host(active, host_registration_id)
        if str(row.get("target_id") or "").strip() or not row.get("tab_kept_reason")
    ]


def inflight_row_for_chat_url(chat_url: str) -> dict[str, Any] | None:
    """Return the in-flight host row for this URL, else an in-flight tab row."""
    from claude_bundles.cdp_registry.execution_state import row_execution_in_flight
    from claude_bundles.cse_url import normalize_cse_url

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
                _events.emit(_events.cdp_tab_host_mint_lock_wait(phase="started"))
            fcntl.flock(fd, fcntl.LOCK_EX)
        with contextlib.suppress(Exception):
            _events.emit(
                _events.cdp_tab_host_mint_lock_wait(
                    phase="acquired",
                    outcome="joined_existing" if started_wait else "minting",
                )
            )
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def set_tab_chat_url(
    registration_id: str,
    *,
    chat_url: str,
    execution_id: str | None = None,
) -> None:
    """Write chat_url onto an existing STATUS_TAB row without touching host rows."""
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


def sweep_kept_tab_rows() -> list[str]:
    """Release kept tab rows whose host is dead, port is down, or a keep reason is set."""
    from claude_bundles.cdp_registry.execution_state import row_execution_in_flight
    from claude_bundles.cse_wake_retain import registration_has_wake_debt

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
        host_dead = (
            not isinstance(host, dict) or host.get("status") not in _CAPACITY_STATUSES
        )
        port = row.get("port")
        port_dead = isinstance(port, int) and not listen(port)
        if row.get("tab_kept_reason") or host_dead or port_dead:
            release_tab_row(str(rid))
            released.append(str(rid))
    return released


def open_tab_row_for_ask(
    *,
    holder: str,
    purpose: str | None,
    mission_kind: str | None,
    parent_thread: str | None,
    host_row: dict[str, Any] | None = None,
    trigger: str = "ask",
    account_id: str | None = None,
) -> Registration:
    """Mint or reuse a pool host, write a tab row, open the CDP target."""
    from .lifecycle import register_lane

    listen = cdp_lane.is_listening
    resolved_account = normalize_account_id(account_id)
    with tab_host_mint_lock():
        active = _store.load_active()
        candidates = cdp_lane.cdp_display_candidates()
        host = host_row or eligible_host(
            active, candidates, listen=listen, account_id=resolved_account
        )
        minted = False
        if host is None:
            host_reg = register_lane(
                holder="cdp-tab-pool",
                purpose="tab_host",
                mission_kind=None,
                parent_thread=None,
                account_id=resolved_account,
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
        resolved_account = normalize_account_id(
            host.get("account_id") or resolved_account
        )
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
                "account_id": resolved_account,
            }
            active[tab_id] = tab_row
            _store.write_active(active)
            _store.append_log("tab_opened", tab_row)
        try:
            target_id = cdp_lane.open_cdp_tab_target(port, "about:blank")
        except Exception:
            release_tab_row(tab_id)
            raise
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

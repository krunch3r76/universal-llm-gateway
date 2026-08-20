"""Dormant CSE seats — URL-durable attendance without a live Chrome process.

A Cowork ``chat_url`` is the identity of a session; the Chrome process and its
CDP port are attach handles. Going dormant kills the process and frees the port
while keeping the URL binding and the seeded profile directory, so relaunch is a
Chrome start plus a cookie reseed rather than a full profile copy.

X clients are consumed per Chrome process, so dormancy — not a larger client
ceiling — is what bounds display occupancy.
"""

from __future__ import annotations

import contextlib
import time
from pathlib import Path
from typing import Any

from claude_bundles import cdp_lane
from claude_bundles import cdp_registry_events as _events
from claude_bundles import cdp_registry_store as _store

from .driver_locks import (
    _release_driver_lock,
    is_driver_lock_held,
    process_holds_driver_lock,
)
from .lifecycle import activate_allocating_row, reserve_allocating_row
from .models import (
    STATUS_DORMANT,
    DormantSeat,
    Registration,
    RegistryError,
    _LaunchFn,
    _ListenFn,
    _row_to_dormant_seat,
    _row_to_registration,
    dormant_max_rows,
    dormant_ttl_s,
)
from .registry_module import registry_package

__all__ = [
    "dormant_candidate_reason",
    "dormant_for_chat_url",
    "host_protection_reason",
    "list_dormant",
    "make_dormant",
    "reclaim_dormant_rows",
    "relaunch_dormant",
]


def list_dormant() -> list[DormantSeat]:
    """Return dormant seats newest first, so the freshest binding wins a tie."""
    rows = [
        _row_to_dormant_seat(row)
        for row in _store.load_active().values()
        if row.get("status") == STATUS_DORMANT and row.get("chat_url")
    ]
    return sorted(rows, key=lambda seat: seat.dormant_at or 0.0, reverse=True)


def dormant_for_chat_url(chat_url: str) -> DormantSeat | None:
    """Return the most recent dormant seat bound to *chat_url*, else None."""
    from claude_bundles.cse_url import normalize_cse_url

    target = normalize_cse_url(chat_url or "")
    if not target:
        return None
    for seat in list_dormant():
        if normalize_cse_url(seat.chat_url) == target:
            return seat
    return None


def host_protection_reason(row: dict[str, Any], *, registration_id: str) -> str | None:
    """Return why this host must keep its Chrome process, or None.

    Wake and stop-ack debt protect a host because the followup that discharges the
    obligation must land on the registered page, and the primary browser is the
    cookie source every relaunch depends on.
    """
    profile = _profile_for_row(row)
    if profile is not None and registry_package().is_primary_profile(profile):
        return "primary_profile"
    if is_driver_lock_held(registration_id) and not process_holds_driver_lock(
        registration_id
    ):
        return "driver_attached"
    from claude_bundles.cse_wake_retain import registration_has_wake_debt

    if registration_has_wake_debt(registration_id):
        return "wake_debt"
    return None


def dormant_candidate_reason(
    row: dict[str, Any], *, registration_id: str
) -> str | None:
    """Return why a live row cannot go dormant, or None when it may."""
    if row.get("status") == STATUS_DORMANT:
        return "already_dormant"
    protection = host_protection_reason(row, registration_id=registration_id)
    if protection is not None:
        return protection
    if not str(row.get("chat_url") or "").strip():
        return "no_chat_url"
    return None


def _profile_for_row(row: dict[str, Any]) -> Path | None:
    raw = row.get("profile")
    if raw:
        return Path(str(raw))
    suffix = row.get("profile_suffix")
    return cdp_lane.profile_for(str(suffix)) if suffix else None


def make_dormant(
    registration_id: str,
    *,
    reason: str = "idle_exit",
    is_listening: _ListenFn | None = None,
) -> DormantSeat | None:
    """Kill the owned Chrome and park the row as dormant; None when refused.

    Refusal is a normal outcome (debt, an attached driver, or no bound URL) and
    leaves the row exactly as it was.
    """
    listen = is_listening or cdp_lane.is_listening
    # Debt and lock probes run before ports.lock: both read other registry files
    # under their own locking, and ports.lock is not re-entrant.
    prior = _store.load_active().get(registration_id)
    if prior is None:
        raise RegistryError(f"unknown registration_id: {registration_id!r}")
    refusal = dormant_candidate_reason(prior, registration_id=registration_id)
    if refusal is not None:
        _store.append_log(
            "dormant_refused",
            {"registration_id": registration_id, "reason": refusal},
        )
        return None

    with _store.ports_lock():
        active = _store.load_active()
        row = active.get(registration_id)
        if row is None or row.get("status") == STATUS_DORMANT:
            return None
        port = row.get("port")
        if isinstance(port, int) and listen(port):
            registry_package()._kill_listener(port)
        updated = dict(row)
        updated["status"] = STATUS_DORMANT
        updated["dormant_at"] = time.time()
        updated["dormant_reason"] = reason
        updated["chrome_pid"] = None
        active[registration_id] = updated
        _store.write_active(active)
        _store.append_log("dormant", updated)
        _release_driver_lock(registration_id)

    seat = _row_to_dormant_seat(updated)
    _record_dormant_episode(seat, reason=reason)
    with contextlib.suppress(Exception):
        _events.emit(
            _events.cdp_port_dormant(
                registration_id=seat.registration_id,
                port=seat.last_port,
                purpose=seat.purpose,
                chat_url=seat.chat_url,
                reason=reason,
            )
        )
    return seat


def _record_dormant_episode(seat: DormantSeat, *, reason: str) -> None:
    """Keep the URL binding evidence-bearing across the process gap."""
    from claude_bundles.cse_provenance import append_episode

    with contextlib.suppress(Exception):
        append_episode(
            chat_url=seat.chat_url,
            registration_id=seat.registration_id,
            cdp_url="",
            lane_thread=None,
            lineage={"parent_thread": seat.parent_thread} if seat.parent_thread else None,
            state=STATUS_DORMANT,
            reason=reason,
        )


def relaunch_dormant(
    registration_id: str,
    *,
    holder: str | None = None,
    launch_chrome: _LaunchFn | None = None,
    is_listening: _ListenFn | None = None,
) -> Registration:
    """Restart a dormant seat's Chrome on a fresh port, reusing its profile."""
    row = _store.load_active().get(registration_id)
    if row is None:
        raise RegistryError(f"unknown registration_id: {registration_id!r}")
    if row.get("status") != STATUS_DORMANT:
        raise RegistryError(
            f"registration {registration_id!r} is {row.get('status')!r}, not dormant"
        )
    listen = is_listening or cdp_lane.is_listening
    launch_fn = launch_chrome or cdp_lane._launch_chrome
    chat_url = str(row.get("chat_url") or "")

    reserved = reserve_allocating_row(
        holder=holder or str(row["holder"]),
        purpose=row.get("purpose"),
        mission_kind=row.get("mission_kind"),
        parent_thread=row.get("parent_thread"),
        listen=listen,
        registration_id=registration_id,
        profile_suffix=str(row["profile_suffix"]),
        carry={
            "chat_url": chat_url,
            "relaunched_from_dormant_at": row.get("dormant_at"),
            "seat_lane": row.get("seat_lane"),
            "seat_closed_at": row.get("seat_closed_at"),
            "seat_bound_at": row.get("seat_bound_at"),
        },
    )
    try:
        chrome_pid = launch_fn(int(reserved["port"]), Path(str(reserved["profile"])))
        activated = activate_allocating_row(
            registration_id, chrome_pid, log_event="relaunch"
        )
    except Exception:
        _restore_dormant(registration_id, row)
        raise

    reg = _row_to_registration(activated)
    with contextlib.suppress(Exception):
        _events.emit(
            _events.cdp_port_relaunched(
                registration_id=reg.registration_id,
                port=reg.port,
                purpose=reg.purpose,
                chat_url=chat_url,
            )
        )
    return reg


def _restore_dormant(registration_id: str, prior: dict[str, Any]) -> None:
    """Put a failed relaunch back to dormant — the URL binding must survive."""
    with contextlib.suppress(Exception):
        with _store.ports_lock():
            active = _store.load_active()
            active[registration_id] = dict(prior)
            _store.write_active(active)
            _store.append_log("dormant_relaunch_failed", dict(prior))
        _release_driver_lock(registration_id)


def reclaim_dormant_rows(
    *,
    now: float | None = None,
    ttl_s: float | None = None,
    max_rows: int | None = None,
) -> list[str]:
    """Drop dormant rows past the TTL or over the row cap; return reclaimed ids.

    Profiles are reclaimed here rather than at dormancy so a seat stays cheap to
    reopen for as long as it is plausibly wanted.
    """
    ts = time.time() if now is None else now
    ttl = dormant_ttl_s() if ttl_s is None else ttl_s
    cap = dormant_max_rows() if max_rows is None else max_rows
    reclaimed: list[str] = []
    with _store.ports_lock():
        active = _store.load_active()
        dormant = sorted(
            (
                (rid, row)
                for rid, row in active.items()
                if row.get("status") == STATUS_DORMANT
            ),
            key=lambda item: float(item[1].get("dormant_at") or 0.0),
            reverse=True,
        )
        for index, (rid, row) in enumerate(dormant):
            age = ts - float(row.get("dormant_at") or ts)
            over_cap = index >= cap
            if age < ttl and not over_cap:
                continue
            updated = dict(row)
            updated["status"] = "released"
            updated["released_at"] = ts
            updated["dormant_reclaim_reason"] = "over_cap" if over_cap else "ttl"
            active[rid] = updated
            reclaimed.append(rid)
            _store.append_log("dormant_reclaimed", updated)
        if reclaimed:
            _store.write_active(active)
    if reclaimed:
        with contextlib.suppress(Exception):
            _events.emit(
                _events.cdp_port_dormant_reclaimed(
                    registration_ids=reclaimed, trigger="hygiene"
                )
            )
    return reclaimed

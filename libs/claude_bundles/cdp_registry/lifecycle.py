"""Allocate, reattach, release Chrome-host rows, and terminate owned listeners while preserving durable registry transitions safely."""

from __future__ import annotations

import contextlib
import os
import time
import uuid
from pathlib import Path
from typing import Any

from claude_bundles import cdp_lane
from claude_bundles import cdp_registry_events as _events
from claude_bundles import cdp_registry_store as _store

from .driver_locks import _claim_driver_lock, _release_driver_lock
from .hygiene import reclaim_best_effort
from .models import (
    MISSION_KINDS,
    STATUS_DORMANT,
    Registration,
    RegistryError,
    _LaunchFn,
    _ListenFn,
    _row_to_registration,
)

# Re-export _used_ports logic via ports module internals
from .ports import _used_ports, _used_suffixes, select_free_registry_port
from .registry_module import registry_package


def _peer_lane_ports() -> set[int]:
    with contextlib.suppress(Exception):
        return set(cdp_lane.held_ports())
    return set()


def _normalize_mission_kind(mission_kind: str | None) -> str | None:
    if mission_kind is None:
        return None
    kind = str(mission_kind).strip().lower()
    if not kind:
        return None
    if kind not in MISSION_KINDS:
        raise RegistryError(
            f"mission_kind must be one of {sorted(MISSION_KINDS)}; got {mission_kind!r}"
        )
    return kind


def _normalize_parent_thread(parent_thread: str | None) -> str | None:
    if parent_thread is None:
        return None
    thread = str(parent_thread).strip()
    return thread or None


def _mint_ids(taken_suffixes: set[str]) -> tuple[str, str]:
    registration_id = uuid.uuid4().hex
    profile_suffix = f"reg-{registration_id[:8]}"
    if profile_suffix in taken_suffixes:
        registration_id = uuid.uuid4().hex
        profile_suffix = f"reg-{registration_id[:8]}"
        if profile_suffix in taken_suffixes:
            raise RegistryError("profile suffix collision; retry")
    return registration_id, profile_suffix


def _rollback_allocating(registration_id: str) -> None:
    with _store.ports_lock():
        active = _store.load_active()
        row = active.pop(registration_id, None)
        if row is not None:
            _store.write_active(active)
            _store.append_log(
                "alloc_failed", {"registration_id": registration_id, **row}
            )
    _release_driver_lock(registration_id)


def reserve_allocating_row(
    *,
    holder: str,
    purpose: str | None,
    mission_kind: str | None,
    parent_thread: str | None,
    listen: _ListenFn,
    registration_id: str | None = None,
    profile_suffix: str | None = None,
    carry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Reserve a free port under ``ports.lock`` and stamp an ``allocating`` row.

    Passing *registration_id* / *profile_suffix* reuses an existing identity — the
    dormant relaunch path keeps its profile and CSE binding through ``carry``.
    """
    with _store.ports_lock():
        active = _store.load_active()
        exclude = _used_ports(active) | _peer_lane_ports()
        port = select_free_registry_port(listen, exclude=exclude)
        if registration_id is None or profile_suffix is None:
            registration_id, profile_suffix = _mint_ids(_used_suffixes(active))
        row: dict[str, Any] = {
            **(carry or {}),
            "registration_id": registration_id,
            "port": port,
            "profile_suffix": profile_suffix,
            "profile": str(cdp_lane.profile_for(profile_suffix)),
            "holder": holder,
            "purpose": purpose,
            "display": cdp_lane.cdp_display(),
            "mission_kind": mission_kind,
            "parent_thread": parent_thread,
            "status": "allocating",
            "chrome_pid": None,
            "holder_pid": os.getpid(),
            "started_at": time.time(),
        }
        active[registration_id] = row
        _store.write_active(active)
        _store.append_log("allocating", row)
        _claim_driver_lock(registration_id)
    return row


def activate_allocating_row(
    registration_id: str, chrome_pid: int | None, *, log_event: str = "register"
) -> dict[str, Any]:
    """Flip a reserved row to ``active`` once Chrome answers on its port."""
    with _store.ports_lock():
        active = _store.load_active()
        current = active.get(registration_id)
        if current is None or current.get("status") != "allocating":
            raise RegistryError(f"registration {registration_id!r} lost during launch")
        current = dict(current)
        current["status"] = "active"
        current["chrome_pid"] = chrome_pid
        active[registration_id] = current
        _store.write_active(active)
        _store.append_log(log_event, current)
    return current


def register_lane(
    *,
    holder: str,
    purpose: str | None = None,
    mission_kind: str | None = None,
    parent_thread: str | None = None,
    launch: bool = True,
    launch_chrome: _LaunchFn | None = None,
    is_listening: _ListenFn | None = None,
) -> Registration:
    """Reserve port under lock, launch Chrome outside lock, then flip active (F1).

    Session address is **not** known at Chrome mint — callers must
    ``bind_session_address`` when the CSE URL is first observed.

    ``mission_kind`` ∈ {root, hop, side, parallel} tags Chrome-host lineage;
    ``parent_thread`` is the bus private-request lane (not SDK ``nest_under``).
    """
    if not holder or not str(holder).strip():
        raise RegistryError("holder is required")
    kind = _normalize_mission_kind(mission_kind)
    parent = _normalize_parent_thread(parent_thread)
    reclaim_best_effort()
    listen = is_listening or cdp_lane.is_listening
    launch_fn = launch_chrome or cdp_lane._launch_chrome

    row = reserve_allocating_row(
        holder=holder,
        purpose=purpose,
        mission_kind=kind,
        parent_thread=parent,
        listen=listen,
    )
    registration_id = str(row["registration_id"])

    chrome_pid: int | None = None
    try:
        if launch:
            chrome_pid = launch_fn(int(row["port"]), Path(str(row["profile"])))
        row = activate_allocating_row(registration_id, chrome_pid)
    except Exception:
        _rollback_allocating(registration_id)
        raise

    reg = _row_to_registration(row)
    _events.emit(_events.cdp_port_registered(reg))
    return reg


def reattach(registration_id: str, *, holder: str) -> Registration:
    """Reattach the same holder while claiming its driver lock under ports.lock."""
    if not holder or not str(holder).strip():
        raise RegistryError("holder is required")
    with _store.ports_lock():
        active = _store.load_active()
        row = active.get(registration_id)
        if row is None:
            raise RegistryError(f"unknown registration_id: {registration_id!r}")
        if row.get("status") != "active":
            raise RegistryError(
                f"registration {registration_id!r} is {row.get('status')!r}, not active"
            )
        if row.get("holder") != holder:
            raise RegistryError(
                f"holder mismatch for {registration_id!r}: "
                f"expected {row.get('holder')!r}, got {holder!r}"
            )
        _claim_driver_lock(registration_id)
        reg = _row_to_registration(row)
    _events.emit(_events.cdp_port_reattached(reg))
    return reg


def deregister_lane(
    registration_id: str,
    *,
    kill: bool | None = None,
    reason: str = "released",
    keep_alive_reason: str | None = None,
    is_listening: _ListenFn | None = None,
) -> None:
    """Release, retain, or orphan-alive a lane; error paths never kill Chrome.

    ``kill=False`` (intentional retention) leaves status ``retained`` — listable,
    reserved, and distinct from hygiene ``orphaned_retry``. ``kill=True`` leaves
    ``released``. Error keep-alive paths leave ``orphaned_alive``.

    Explicit ``kill=True`` still kills residual Chrome on ``orphaned_alive`` /
    ``retained`` (and on already-``released`` rows) — mission cull must not no-op.
    """
    listen = is_listening or cdp_lane.is_listening
    error_release = keep_alive_reason is not None or reason in {
        "cse_not_found",
        "probe_failed",
    }
    if kill is None:
        kill = reason == "released" and not error_release
    if kill:
        # Explicit kill wins over probe_failed / keep-alive retain.
        error_release = False
    with _store.ports_lock():
        active = _store.load_active()
        row = active.get(registration_id)
        if row is None:
            raise RegistryError(f"unknown registration_id: {registration_id!r}")
        status = row.get("status")
        if status == "released":
            if kill:
                port = int(row["port"])
                if listen(port):
                    registry_package()._kill_listener(port)
            _release_driver_lock(registration_id)
            return
        if status == STATUS_DORMANT:
            # The recorded port is historical: another host may own it now, so a
            # kill here could take down an unrelated Chrome.
            _release_driver_lock(registration_id)
            return
        if status in {"orphaned_alive", "retained"} and not kill:
            _release_driver_lock(registration_id)
            return

        port = int(row["port"])
        if kill and listen(port):
            registry_package()._kill_listener(port)

        row = dict(row)
        if error_release:
            row["status"] = "orphaned_alive"
            row["orphaned_at"] = time.time()
            row["orphan_reason"] = keep_alive_reason or reason
        elif not kill:
            row["status"] = "retained"
            row["retained_at"] = time.time()
            row["retain_reason"] = (
                reason if reason not in {"released", "retained"} else "kill_false_exit"
            )
        else:
            row["status"] = "released"
            row["released_at"] = time.time()
        active[registration_id] = row
        _store.write_active(active)
        _store.append_log("deregister", row)
        _release_driver_lock(registration_id)

    _events.emit(_events.cdp_port_deregistered(_row_to_registration(row)))


def _kill_listener(port: int) -> None:
    import subprocess

    try:
        out = subprocess.check_output(
            ["ss", "-ltnpH", f"sport = :{port}"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return
    for tok in out.replace(",", " ").split():
        if tok.startswith("pid="):
            with contextlib.suppress(ValueError, ProcessLookupError, PermissionError):
                os.kill(int(tok.split("=", 1)[1].split(",")[0]), 15)
            return

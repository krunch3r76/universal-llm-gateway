"""Explicit CDP port registration — agent-bus-thread semantics for Chrome lanes.

Supersedes hand-picked ``--cdp-url`` / warm-reuse for automated seats.
``:9222`` remains the attended primary (out of pool). Persistence:
``cdp_registry_store``; Chrome launch/seed from ``cdp_lane``.

v1: ports in ``allocating`` / ``active`` / ``released`` stay out of the free
pool until ``hygiene_reclaim_released`` drops released rows.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import shutil
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from claude_bundles import cdp_lane
from claude_bundles import cdp_registry_events as _events
from claude_bundles import cdp_registry_store as _store

# Re-export paths for tests / callers that patch registry location.
REGISTRY_DIR = _store.REGISTRY_DIR
REGISTRY_LOG = _store.REGISTRY_LOG
ACTIVE_JSON = _store.ACTIVE_JSON
PORTS_LOCK = _store.PORTS_LOCK
REGISTRATIONS_DIR = _store.REGISTRATIONS_DIR

PORT_RANGE = range(9223, 9350)
_RESERVED_STATUSES = frozenset({"allocating", "active", "released"})

_LaunchFn = Callable[[int, Path], int]
_ListenFn = Callable[[int], bool]
_HELD_LOCKS: dict[str, int] = {}


class RegistryError(RuntimeError):
    """Base for CDP port-registry failures."""


class RegistryBusyError(RegistryError):
    """Second driver attach to an already-held registration_id."""


class RegistryExhaustedError(RegistryError):
    """No free port left in the configured pool."""


@dataclass(frozen=True)
class Registration:
    registration_id: str
    port: int
    profile_suffix: str
    profile: Path
    cdp_url: str
    holder: str
    purpose: str | None = None


@dataclass(frozen=True)
class HygieneReclaimResult:
    reclaimed_ports: list[int]
    removed_profiles: list[str]


def _claim_driver_lock(registration_id: str) -> int:
    if registration_id in _HELD_LOCKS:
        raise RegistryBusyError(
            f"registration {registration_id!r} already held by this process"
        )
    fd = _store.open_lock(_store.registration_lock_path(registration_id))
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(fd)
        raise RegistryBusyError(
            f"registration {registration_id!r} already has an attached driver"
        ) from exc
    _HELD_LOCKS[registration_id] = fd
    return fd


def _release_driver_lock(registration_id: str) -> None:
    fd = _HELD_LOCKS.pop(registration_id, None)
    if fd is None:
        return
    with contextlib.suppress(OSError):
        fcntl.flock(fd, fcntl.LOCK_UN)
    with contextlib.suppress(OSError):
        os.close(fd)


def _used_ports(active: dict[str, dict[str, Any]]) -> set[int]:
    used: set[int] = set()
    for row in active.values():
        if row.get("status") in _RESERVED_STATUSES:
            port = row.get("port")
            if isinstance(port, int):
                used.add(port)
    return used


def used_ports_snapshot() -> set[int]:
    """Registry-reserved ports for cross-allocator exclusion (cdp_lane / F3)."""
    return _used_ports(_store.load_active())


def _used_suffixes(active: dict[str, dict[str, Any]]) -> set[str]:
    return {
        str(row["profile_suffix"])
        for row in active.values()
        if row.get("profile_suffix")
    }


def _peer_lane_ports() -> set[int]:
    with contextlib.suppress(Exception):
        return set(cdp_lane.held_ports())
    return set()


def select_free_registry_port(
    is_listening: _ListenFn,
    *,
    exclude: set[int],
    port_range: range | None = None,
) -> int:
    port_range = PORT_RANGE if port_range is None else port_range
    for port in port_range:
        if port in exclude:
            continue
        if not is_listening(port):
            return port
    raise RegistryExhaustedError(
        f"no free CDP port in {port_range.start}-{port_range.stop - 1} "
        f"(active+released+allocating excluded; run hygiene to reclaim released)"
    )


def _row_to_registration(row: dict[str, Any]) -> Registration:
    suffix = str(row["profile_suffix"])
    port = int(row["port"])
    return Registration(
        registration_id=str(row["registration_id"]),
        port=port,
        profile_suffix=suffix,
        profile=cdp_lane.profile_for(suffix),
        cdp_url=f"http://127.0.0.1:{port}",
        holder=str(row["holder"]),
        purpose=row.get("purpose"),
    )


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


def register_lane(
    *,
    holder: str,
    purpose: str | None = None,
    launch: bool = True,
    launch_chrome: _LaunchFn | None = None,
    is_listening: _ListenFn | None = None,
) -> Registration:
    """Reserve port under lock, launch Chrome outside lock, then flip active (F1)."""
    if not holder or not str(holder).strip():
        raise RegistryError("holder is required")
    listen = is_listening or cdp_lane.is_listening
    launch_fn = launch_chrome or cdp_lane._launch_chrome

    with _store.ports_lock():
        active = _store.load_active()
        exclude = _used_ports(active) | _peer_lane_ports()
        port = select_free_registry_port(listen, exclude=exclude)
        registration_id, profile_suffix = _mint_ids(_used_suffixes(active))
        profile = cdp_lane.profile_for(profile_suffix)
        row = {
            "registration_id": registration_id,
            "port": port,
            "profile_suffix": profile_suffix,
            "profile": str(profile),
            "holder": holder,
            "purpose": purpose,
            "status": "allocating",
            "chrome_pid": None,
            "holder_pid": os.getpid(),
            "started_at": time.time(),
        }
        active[registration_id] = row
        _store.write_active(active)
        _store.append_log("allocating", row)
        _claim_driver_lock(registration_id)

    chrome_pid: int | None = None
    try:
        if launch:
            chrome_pid = launch_fn(port, profile)
        with _store.ports_lock():
            active = _store.load_active()
            current = active.get(registration_id)
            if current is None or current.get("status") != "allocating":
                raise RegistryError(
                    f"registration {registration_id!r} lost during launch"
                )
            current = dict(current)
            current["status"] = "active"
            current["chrome_pid"] = chrome_pid
            active[registration_id] = current
            _store.write_active(active)
            _store.append_log("register", current)
            row = current
    except Exception:
        _rollback_allocating(registration_id)
        raise

    reg = _row_to_registration(row)
    _events.emit(_events.cdp_port_registered(reg))
    return reg


def reattach(registration_id: str, *, holder: str) -> Registration:
    """Same-holder reattach; driver lock claimed under ports.lock (F2)."""
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
    kill: bool = False,
    is_listening: _ListenFn | None = None,
) -> None:
    """Mark released (not free). Optional --kill of Chrome listener."""
    listen = is_listening or cdp_lane.is_listening
    with _store.ports_lock():
        active = _store.load_active()
        row = active.get(registration_id)
        if row is None:
            raise RegistryError(f"unknown registration_id: {registration_id!r}")
        if row.get("status") == "released":
            _release_driver_lock(registration_id)
            return

        port = int(row["port"])
        if kill and listen(port):
            _kill_listener(port)

        row = dict(row)
        row["status"] = "released"
        row["released_at"] = time.time()
        active[registration_id] = row
        _store.write_active(active)
        _store.append_log("deregister", row)
        _release_driver_lock(registration_id)

    _events.emit(_events.cdp_port_deregistered(_row_to_registration(row)))


def list_active() -> list[Registration]:
    active = _store.load_active()
    out = [
        _row_to_registration(row)
        for row in active.values()
        if row.get("status") == "active"
    ]
    return sorted(out, key=lambda r: r.port)


def _profile_path_from_row(row: dict[str, Any]) -> Path | None:
    raw = row.get("profile")
    if raw:
        return Path(str(raw))
    suffix = row.get("profile_suffix")
    if suffix:
        return cdp_lane.profile_for(str(suffix))
    return None


def _maybe_rmtree_released_profile(
    row: dict[str, Any],
    *,
    chrome_port_for_profile: Callable[[Path], int | None] | None = None,
) -> Path | None:
    """Remove a released reg profile dir when it is not PRIMARY and Chrome is not live."""
    probe = chrome_port_for_profile or cdp_lane.chrome_port_for_profile
    profile = _profile_path_from_row(row)
    if profile is None or not profile.exists():
        return None
    if profile.resolve() == cdp_lane.PRIMARY_PROFILE.resolve():
        return None
    if probe(profile) is not None:
        return None
    shutil.rmtree(profile)
    return profile


def hygiene_reclaim_released() -> HygieneReclaimResult:
    reclaimed: list[int] = []
    removed: list[str] = []
    with _store.ports_lock():
        active = _store.load_active()
        keep: dict[str, dict[str, Any]] = {}
        for rid, row in active.items():
            if row.get("status") == "released":
                reclaimed.append(int(row["port"]))
                removed_path = _maybe_rmtree_released_profile(row)
                if removed_path is not None:
                    removed.append(str(removed_path))
                _store.append_log(
                    "hygiene_reclaim",
                    {
                        "registration_id": rid,
                        **row,
                        "profile_removed": removed_path is not None,
                    },
                )
            else:
                keep[rid] = row
        _store.write_active(keep)
    return HygieneReclaimResult(
        reclaimed_ports=sorted(reclaimed),
        removed_profiles=sorted(removed),
    )


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


def is_driver_lock_held(registration_id: str) -> bool:
    """True if another process holds the driver flock (LOCK_NB probe, no claim)."""
    lock_path = _store.registration_lock_path(registration_id)
    if not lock_path.exists():
        return False
    fd = _store.open_lock(lock_path)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        with contextlib.suppress(OSError):
            os.close(fd)


def process_holds_driver_lock(registration_id: str) -> bool:
    """True iff this process claimed the driver lock via register/reattach."""
    return registration_id in _HELD_LOCKS


def registration_as_dict(reg: Registration) -> dict[str, Any]:
    d = asdict(reg)
    d["profile"] = str(reg.profile)
    return d


def active_registration_dicts() -> list[dict[str, Any]]:
    """List projection for CLI — includes driver_pid, attached, chrome_pid."""
    active = _store.load_active()
    out: list[dict[str, Any]] = []
    for rid, row in active.items():
        if row.get("status") != "active":
            continue
        d = registration_as_dict(_row_to_registration(row))
        holder_pid = row.get("holder_pid")
        d["driver_pid"] = holder_pid if isinstance(holder_pid, int) else None
        d["attached"] = is_driver_lock_held(rid)
        chrome_pid = row.get("chrome_pid")
        d["chrome_pid"] = chrome_pid if isinstance(chrome_pid, int) else None
        out.append(d)
    return sorted(out, key=lambda item: int(item["port"]))


# Test helpers — load active via store (kept for monkeypatched paths).
def _load_active() -> dict[str, dict[str, Any]]:
    return _store.load_active()

"""Allocate, reattach, release Chrome-host rows, and terminate owned listeners while preserving durable registry transitions safely."""

from __future__ import annotations

import contextlib
import os
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from universal_protocol.errors import ProtocolError

from claude_bundles import cdp_lane
from claude_bundles import cdp_registry_events as _events
from claude_bundles import cdp_registry_store as _store

from .driver_locks import (
    _claim_driver_lock,
    _release_driver_lock,
    is_driver_lock_held,
    process_holds_driver_lock,
)
from .hygiene import reclaim_best_effort, reclaim_profile_for_detached_row
from .models import (
    MISSION_KINDS,
    STATUS_DORMANT,
    JoinedReentryExhausted,
    Registration,
    RegistryError,
    SeatContended,
    _LaunchFn,
    _ListenFn,
    _row_to_registration,
    seat_open,
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
            port = row.get("port")
            _events.emit(
                _events.cdp_port_alloc_failed(
                    registration_id=registration_id,
                    port=port if isinstance(port, int) else None,
                    parent_thread=row.get("parent_thread"),
                    holder=row.get("holder"),
                )
            )
    _release_driver_lock(registration_id)


def _operator_join_candidate(row: dict[str, Any]) -> bool:
    rid = str(row.get("registration_id") or "").strip()
    if not rid:
        return False
    return process_holds_driver_lock(rid) or is_driver_lock_held(rid)


def _join_target(active: dict[str, Any], parent: str) -> dict[str, Any] | None:
    """Smallest-started allocating row for *parent* that a driver still holds."""
    from .driving_seat import driving_lane_census

    eligible = [
        row
        for row in driving_lane_census(active, parent)["allocating"]
        if _operator_join_candidate(row)
    ]
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda row: (
            float(row.get("started_at") or 0.0),
            str(row.get("registration_id") or ""),
        ),
    )


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
    expect_status: str | None = None,
    launch: bool = True,
    join: bool = True,
) -> tuple[dict[str, Any], bool]:
    """Reserve a port under ``ports.lock``, or join an in-flight allocating row.

    Returns ``(row, True)`` when this caller minted. Returns ``(row, False)``
    when this caller joined a row whose driver lock is already held — no port
    select, no write, no log line. A stranded allocating row (neither lock
    held) is not a join target.

    Passing *registration_id* / *profile_suffix* reuses an existing identity —
    the dormant relaunch path keeps its profile and CSE binding through
    ``carry``. *expect_status* compare-and-set runs before any claim or write.
    """
    from claude_bundles.what_is_running_view import OPERATOR_PURPOSES
    from claude_bundles.x_display_capacity import require_chrome_headroom

    with _store.ports_lock():
        active = _store.load_active()
        parent = str(parent_thread or "").strip()
        purpose_norm = str(purpose or "").strip()
        kind_norm = str(mission_kind or "").strip().lower()
        if (
            join
            and registration_id is None
            and parent
            and purpose_norm in OPERATOR_PURPOSES
            and kind_norm != "hop"
        ):
            target = _join_target(active, parent)
            if target is not None:
                return target, False
        if expect_status is not None:
            current = active.get(registration_id) if registration_id else None
            current_status = (
                current.get("status") if isinstance(current, dict) else None
            )
            if current_status != expect_status:
                raise SeatContended(
                    f"registration {registration_id!r} is {current_status!r}, "
                    f"not {expect_status!r}"
                )
        if launch:
            reserved_chromes = sum(
                1
                for row in active.values()
                if isinstance(row, dict) and row.get("status") == "allocating"
            )
            require_chrome_headroom(reserved_chromes=reserved_chromes)
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
        for seat_key in ("seat_lane", "seat_bound_at", "seat_closed_at"):
            row.pop(seat_key, None)
        _claim_driver_lock(registration_id)
        active[registration_id] = row
        _store.write_active(active)
        _store.append_log("allocating", row)
        _events.emit(
            _events.cdp_port_allocating(
                registration_id=registration_id,
                port=port,
                parent_thread=parent_thread if isinstance(parent_thread, str) else None,
                holder=holder,
            )
        )
    return row, True


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


def _finish_reserved_launch(
    row: dict[str, Any],
    *,
    launch: bool,
    launch_fn: _LaunchFn,
) -> Registration:
    """Launch outside the ports lock, then flip the reserved row to active."""
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


def _mint_after_join(
    *,
    holder: str,
    purpose: str | None,
    mission_kind: str | None,
    parent_thread: str | None,
    listen: _ListenFn,
    launch_fn: _LaunchFn,
    join: bool,
) -> Registration:
    """One reserve re-entry. ``join=False`` is the stranded-row mint only."""
    row, minted = reserve_allocating_row(
        holder=holder,
        purpose=purpose,
        mission_kind=mission_kind,
        parent_thread=parent_thread,
        listen=listen,
        launch=True,
        join=join,
    )
    if not minted:
        return _wait_for_joined_row(
            row,
            holder=holder,
            purpose=purpose,
            mission_kind=mission_kind,
            parent_thread=parent_thread,
            listen=listen,
            launch_fn=launch_fn,
            joined=None,
        )
    return _finish_reserved_launch(row, launch=True, launch_fn=launch_fn)


# Test seam only: called with the joined registration_id between the two wait reads.
_between_wait_observations: Callable[[str], None] | None = None


def _wait_for_joined_row(
    row: dict[str, Any],
    *,
    holder: str,
    purpose: str | None,
    mission_kind: str | None,
    parent_thread: str | None,
    listen: _ListenFn,
    launch_fn: _LaunchFn,
    joined: list | None,
    reentry: bool = False,
) -> Registration:
    """Poll until the joined row leaves ``allocating``. No deadline.

    The same iteration reads the driver lock before the status. Both reads
    stay outside ``ports_lock``. A nested re-entry raises
    ``JoinedReentryExhausted`` instead of reserving or waiting again.
    The wait does not take a blocking flock and does not raise ``LaneError``.
    ``_LAUNCH_WAIT_S`` stays inside ``_launch_chrome`` only.
    """
    registration_id = str(row["registration_id"])
    while True:
        held = process_holds_driver_lock(registration_id) or is_driver_lock_held(
            registration_id
        )
        observer = _between_wait_observations
        if observer is not None:
            observer(registration_id)
        current = _store.load_active().get(registration_id)
        status = current.get("status") if isinstance(current, dict) else None
        if status == "allocating" and held:
            time.sleep(cdp_lane._POLL_MS / 1000)
            continue
        if status == "active" and isinstance(current, dict):
            if joined is not None:
                joined.append(registration_id)
            return _row_to_registration(current)
        if reentry:
            raise JoinedReentryExhausted(
                f"registration {registration_id!r} joined re-entry exhausted "
                f"(status={status!r})"
            )
        if status == "allocating":
            return _mint_after_join(
                holder=holder,
                purpose=purpose,
                mission_kind=mission_kind,
                parent_thread=parent_thread,
                listen=listen,
                launch_fn=launch_fn,
                join=False,
            )
        re_row, minted = reserve_allocating_row(
            holder=holder,
            purpose=purpose,
            mission_kind=mission_kind,
            parent_thread=parent_thread,
            listen=listen,
            launch=True,
            join=True,
        )
        if minted:
            return _finish_reserved_launch(re_row, launch=True, launch_fn=launch_fn)
        return _wait_for_joined_row(
            re_row,
            holder=holder,
            purpose=purpose,
            mission_kind=mission_kind,
            parent_thread=parent_thread,
            listen=listen,
            launch_fn=launch_fn,
            joined=joined,
            reentry=True,
        )


def register_lane(
    *,
    holder: str,
    purpose: str | None = None,
    mission_kind: str | None = None,
    parent_thread: str | None = None,
    launch: bool = True,
    launch_chrome: _LaunchFn | None = None,
    is_listening: _ListenFn | None = None,
    joined: list | None = None,
) -> Registration:
    """Reserve port under lock, launch Chrome outside lock, then flip active (F1).

    Same-parent operator mints join an allocating row whose driver lock is
    held and wait on the bind worker until that row is active. The wait has
    no deadline and does not raise ``LaneError``.

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
    row, minted = reserve_allocating_row(
        holder=holder,
        purpose=purpose,
        mission_kind=kind,
        parent_thread=parent,
        listen=listen,
        launch=launch,
        join=True,
    )
    if not minted:
        return _wait_for_joined_row(
            row,
            holder=holder,
            purpose=purpose,
            mission_kind=kind,
            parent_thread=parent,
            listen=listen,
            launch_fn=launch_fn,
            joined=joined,
        )
    return _finish_reserved_launch(row, launch=launch, launch_fn=launch_fn)


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
                if seat_open(row):
                    _store.append_log(
                        "deregister_kill_refused_seat_open",
                        {
                            "registration_id": registration_id,
                            "port": row.get("port"),
                            "seat_lane": row.get("seat_lane"),
                        },
                    )
                    _release_driver_lock(registration_id)
                    return
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


def detach(registration_id: str, *, reason: str) -> dict[str, Any]:
    """Pop a superseded hop row after stand-down token and Chrome kill."""
    rid = (registration_id or "").strip()
    if not rid:
        raise ProtocolError(
            code="attachment.not_attached",
            message="registration_id required",
            source="rpc",
            retryable=False,
            data={},
        )
    _store.require_seat_authority(operation="detach")
    listen = cdp_lane.is_listening
    chat_url: str | None = None
    profile_outcome: str | None = None
    with _store.ports_lock():
        active = _store.load_active()
        row = active.get(rid)
        if not isinstance(row, dict):
            raise ProtocolError(
                code="attachment.not_attached",
                message=f"unknown registration_id: {rid!r}",
                source="rpc",
                retryable=False,
                data={"registration_id": rid},
            )
        if seat_open(row):
            raise ProtocolError(
                code="seat.open_on_detach",
                message=f"seat still open on {rid!r}",
                source="rpc",
                retryable=False,
                data={"registration_id": rid, "seat_lane": row.get("seat_lane")},
            )
        if row.get("superseded_by") and not _store.has_standdown_token(rid):
            raise ProtocolError(
                code="standdown.missing",
                message="stand-down token required before detach",
                source="rpc",
                retryable=False,
                data={"registration_id": rid},
            )
        chat_url = str(row.get("chat_url") or "").strip() or None
        row_copy = dict(row)
        port = row.get("port")
        if isinstance(port, int) and listen(port):
            registry_package()._kill_listener(port)
        profile_outcome = reclaim_profile_for_detached_row(rid, row_copy)
        detached_at = time.time()
        log_row = {
            **row_copy,
            "detached_at": detached_at,
            "detach_reason": reason,
        }
        if profile_outcome not in ("success", "missing"):
            log_row["profile_reclaim_outcome"] = profile_outcome
            log_row["profile_suffix"] = row_copy.get("profile_suffix")
        _store.append_log("detached", log_row)
        from claude_bundles.cse_provenance import append_episode

        if chat_url:
            append_episode(
                chat_url=chat_url,
                registration_id=rid,
                cdp_url=f"http://127.0.0.1:{port}" if isinstance(port, int) else "",
                state="detached",
                reason=reason,
            )
        active.pop(rid, None)
        _store.write_active(active)
    from services.git_integration_worker.cse_session_holders import (
        release_holder_remote,
    )

    if chat_url:
        release_holder_remote(chat_url=chat_url, registration_id=rid, reason=reason)
    with contextlib.suppress(Exception):
        _events.emit(
            _events.cdp_attachment_detached(
                registration_id=rid,
                chat_url=chat_url,
                reason=reason,
            )
        )
    return {
        "registration_id": rid,
        "chat_url": chat_url,
        "reason": reason,
        "profile_reclaim": profile_outcome,
    }

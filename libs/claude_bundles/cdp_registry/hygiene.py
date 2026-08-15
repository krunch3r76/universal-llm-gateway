"""Manage profile trash, stale and orphan reclaim, and retained registry statuses without erasing durable history."""

from __future__ import annotations

import contextlib
import os
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from claude_bundles import cdp_lane, cdp_lane_reaper
from claude_bundles import cdp_registry_store as _store

from .models import (
    _RECLAIMABLE_STATUSES,
    STALE_ACTIVE_TTL_S,
    HygieneReclaimResult,
    _ListenFn,
)
from .registry_module import registry_package

RECLAIM_TRASH_DIR = Path.home() / ".gateway" / ".reclaim-trash"


def _profile_path_from_row(row: dict[str, Any]) -> Path | None:
    raw = row.get("profile")
    if raw:
        return Path(str(raw))
    suffix = row.get("profile_suffix")
    if suffix:
        return cdp_lane.profile_for(str(suffix))
    return None


def is_primary_profile(profile: Path) -> bool:
    """True when *profile* resolves to the attended primary Chrome profile path."""
    return profile.resolve() == cdp_lane.PRIMARY_PROFILE.resolve()


def _pid_alive(pid: int) -> bool:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.kill(pid, 0)
        return True
    return False


def _reclaim_profile_to_trash(
    profile: Path,
    trash_id: str,
    *,
    chrome_port_for_profile: Callable[[Path], int | None] | None = None,
) -> str:
    """Move a profile into reclaim trash; never touch PRIMARY."""
    probe = chrome_port_for_profile or cdp_lane.chrome_port_for_profile
    if not profile.exists():
        return "missing"
    if is_primary_profile(profile):
        return "skipped_primary"
    if probe(profile) is not None:
        return "skipped_live"
    reclaim_trash_dir = registry_package().RECLAIM_TRASH_DIR
    reclaim_trash_dir.mkdir(parents=True, exist_ok=True)
    dest = reclaim_trash_dir / trash_id
    if dest.exists():
        shutil.rmtree(dest)
    shutil.move(str(profile), str(dest))
    return "success"


def _empty_reclaim_trash() -> list[str]:
    removed: list[str] = []
    reclaim_trash_dir = registry_package().RECLAIM_TRASH_DIR
    if not reclaim_trash_dir.exists():
        return removed
    for entry in reclaim_trash_dir.iterdir():
        if entry.is_dir():
            shutil.rmtree(entry)
            removed.append(str(entry))
    return removed


def _orphan_profile_sweep(
    active: dict[str, dict[str, Any]],
    *,
    chrome_port_for_profile: Callable[[Path], int | None] | None = None,
) -> list[str]:
    probe = chrome_port_for_profile or cdp_lane.chrome_port_for_profile
    parent = cdp_lane.PRIMARY_PROFILE.parent
    profile_prefix = f"{cdp_lane.PRIMARY_PROFILE.name}-reg-"
    registered_suffixes = {
        str(row["profile_suffix"])
        for row in active.values()
        if row.get("profile_suffix")
    }
    removed: list[str] = []
    for path in parent.glob(f"{profile_prefix}*"):
        if is_primary_profile(path):
            continue
        suffix = path.name.removeprefix(f"{cdp_lane.PRIMARY_PROFILE.name}-")
        if suffix in registered_suffixes:
            continue
        if probe(path) is not None:
            continue
        if (
            _reclaim_profile_to_trash(
                path, f"orphan-{suffix}", chrome_port_for_profile=probe
            )
            == "success"
        ):
            removed.append(str(path))
    return removed


def _reap_stale_active_rows(
    active: dict[str, dict[str, Any]],
    listen: _ListenFn,
    *,
    now: float | None = None,
) -> list[str]:
    ts = time.time() if now is None else now
    reaped: list[str] = []
    for rid, row in active.items():
        if row.get("status") != "active":
            continue
        started = row.get("started_at")
        if not isinstance(started, (int, float)):
            continue
        if ts - float(started) < STALE_ACTIVE_TTL_S:
            continue
        holder_pid = row.get("holder_pid")
        if isinstance(holder_pid, int) and _pid_alive(holder_pid):
            continue
        port = row.get("port")
        if isinstance(port, int) and listen(port):
            continue
        updated = dict(row)
        updated["status"] = "released"
        updated["released_at"] = ts
        updated["reaped_stale_active"] = True
        active[rid] = updated
        reaped.append(rid)
        _store.append_log("stale_active_reap", updated)
    return reaped


def _reclaim_row_profile(
    rid: str,
    row: dict[str, Any],
    *,
    chrome_port_for_profile: Callable[[Path], int | None] | None = None,
) -> tuple[str, Path | None]:
    profile = _profile_path_from_row(row)
    if profile is None:
        return "missing", None
    outcome = _reclaim_profile_to_trash(
        profile, rid, chrome_port_for_profile=chrome_port_for_profile
    )
    if outcome == "success":
        return "success", profile
    return outcome, profile


def hygiene_reclaim_extended(
    *,
    include_stale_active: bool = True,
    include_orphan_sweep: bool = True,
    empty_trash: bool = True,
    is_listening: _ListenFn | None = None,
    chrome_port_for_profile: Callable[[Path], int | None] | None = None,
) -> HygieneReclaimResult:
    """Reclaim released/orphaned rows and optionally sweep stale active or orphan profiles."""
    listen = is_listening or cdp_lane.is_listening
    reclaimed: list[int] = []
    removed: list[str] = []
    with _store.ports_lock():
        active = _store.load_active()
        if include_stale_active:
            _reap_stale_active_rows(active, listen)
        cdp_lane_reaper.reap_orphaned_alive_rows(
            active,
            listen,
            kill_listener=registry_package()._kill_listener,
            include_ttl_reap=include_stale_active,
        )
        keep: dict[str, dict[str, Any]] = {}
        for rid, row in active.items():
            status = row.get("status")
            if status == "retained":
                # Intentional retention: never stamp orphaned_retry over it.
                # Reclaim only when glass/profile is dead (retention ended).
                outcome, profile = _reclaim_row_profile(
                    rid,
                    row,
                    chrome_port_for_profile=chrome_port_for_profile,
                )
                if outcome in ("skipped_live", "skipped_primary"):
                    keep[rid] = row
                    _store.append_log(
                        "hygiene_retained_kept",
                        {
                            "registration_id": rid,
                            **row,
                            "skip_reason": outcome,
                        },
                    )
                elif outcome in ("success", "missing"):
                    reclaimed.append(int(row["port"]))
                    if outcome == "success" and profile is not None:
                        removed.append(str(profile))
                    _store.append_log(
                        "hygiene_reclaim",
                        {
                            "registration_id": rid,
                            "port": row["port"],
                            "profile_suffix": row.get("profile_suffix"),
                            "profile": row.get("profile"),
                            "holder": row.get("holder"),
                            "status": "reclaimed",
                            "reclaim_outcome": outcome,
                            "profile_removed": outcome == "success",
                            "prior_status": "retained",
                        },
                    )
                else:
                    keep[rid] = row
            elif status in _RECLAIMABLE_STATUSES:
                outcome, profile = _reclaim_row_profile(
                    rid,
                    row,
                    chrome_port_for_profile=chrome_port_for_profile,
                )
                if outcome in ("skipped_live", "skipped_primary"):
                    updated = dict(row)
                    updated["status"] = "orphaned_retry"
                    keep[rid] = updated
                    _store.append_log(
                        "hygiene_orphaned_retry",
                        {"registration_id": rid, **updated, "skip_reason": outcome},
                    )
                elif outcome in ("success", "missing"):
                    reclaimed.append(int(row["port"]))
                    if outcome == "success" and profile is not None:
                        removed.append(str(profile))
                    _store.append_log(
                        "hygiene_reclaim",
                        {
                            "registration_id": rid,
                            "port": row["port"],
                            "profile_suffix": row.get("profile_suffix"),
                            "profile": row.get("profile"),
                            "holder": row.get("holder"),
                            "status": "reclaimed",
                            "reclaim_outcome": outcome,
                            "profile_removed": outcome == "success",
                        },
                    )
                else:
                    keep[rid] = row
            else:
                keep[rid] = row
        if include_orphan_sweep:
            removed.extend(
                _orphan_profile_sweep(
                    keep, chrome_port_for_profile=chrome_port_for_profile
                )
            )
        _store.write_active(keep)
    if empty_trash:
        _empty_reclaim_trash()
    return HygieneReclaimResult(
        reclaimed_ports=sorted(reclaimed),
        removed_profiles=sorted(set(removed)),
    )


def reclaim_best_effort() -> None:
    """Run hygiene reclaim without stale-active reaping while suppressing cleanup errors."""
    with contextlib.suppress(Exception):
        hygiene_reclaim_extended(include_stale_active=False)


def hygiene_reclaim_released() -> HygieneReclaimResult:
    """Reclaim all released and orphaned_retry registry rows with default hygiene options."""
    return hygiene_reclaim_extended()

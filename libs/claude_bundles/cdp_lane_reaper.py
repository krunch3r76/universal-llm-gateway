"""Exit path for ``orphaned_alive`` CDP registry rows — grace TTL + dead detection."""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from claude_bundles import cdp_lane
from claude_bundles import cdp_registry_events as _events

_DEFAULT_ORPHANED_ALIVE_TTL_S = 1800.0  # 30 min — exceeds warm-reattach window


def orphaned_alive_ttl_s() -> float:
    """TTL for unattached ``orphaned_alive`` rows before kill+release."""
    raw = os.environ.get("CDP_ORPHANED_ALIVE_TTL_S", "").strip()
    if not raw:
        return _DEFAULT_ORPHANED_ALIVE_TTL_S
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_ORPHANED_ALIVE_TTL_S
    return value if value > 0 else _DEFAULT_ORPHANED_ALIVE_TTL_S


ORPHANED_ALIVE_TTL_S = _DEFAULT_ORPHANED_ALIVE_TTL_S


def _profile_path_from_row(row: dict[str, Any]) -> Path | None:
    raw = row.get("profile")
    if raw:
        return Path(str(raw))
    suffix = row.get("profile_suffix")
    if suffix:
        return cdp_lane.profile_for(str(suffix))
    return None


def _is_primary_row(row: dict[str, Any]) -> bool:
    profile = _profile_path_from_row(row)
    if profile is None:
        return False
    return profile.resolve() == cdp_lane.PRIMARY_PROFILE.resolve()


def _default_pid_alive(pid: int) -> bool:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.kill(pid, 0)
        return True
    return False


def _default_is_attached(registration_id: str) -> bool:
    from claude_bundles import cdp_registry

    return cdp_registry.is_driver_lock_held(registration_id)


def _emit_reaped(row: dict[str, Any], *, trigger: str) -> None:
    with contextlib.suppress(Exception):
        _events.emit(
            _events.cdp_port_orphaned_alive_reaped(
                registration_id=str(row["registration_id"]),
                port=int(row["port"]),
                trigger=trigger,
                reaped_orphaned_alive=row.get("reaped_orphaned_alive"),
            )
        )


def _release_orphaned_row(
    active: dict[str, dict[str, Any]],
    rid: str,
    row: dict[str, Any],
    *,
    ts: float,
    trigger: str,
    reaped_kind: str,
) -> None:
    updated = dict(row)
    updated["status"] = "released"
    updated["released_at"] = ts
    updated["reaped_orphaned_alive"] = reaped_kind
    active[rid] = updated
    _emit_reaped(updated, trigger=trigger)


def reap_orphaned_alive_rows(
    active: dict[str, dict[str, Any]],
    listen: Callable[[int], bool],
    *,
    now: float | None = None,
    pid_alive: Callable[[int], bool] | None = None,
    kill_listener: Callable[[int], None] | None = None,
    is_attached: Callable[[str], bool] | None = None,
    include_ttl_reap: bool = True,
) -> list[str]:
    """Transition dead / expired ``orphaned_alive`` rows to ``released``.

    Mutates *active* in place; returns reaped registration_ids.
    """
    ts = time.time() if now is None else now
    alive = pid_alive or _default_pid_alive
    attached = is_attached or _default_is_attached
    ttl = orphaned_alive_ttl_s()
    reaped: list[str] = []

    try:
        for rid, row in list(active.items()):
            with contextlib.suppress(Exception):
                if row.get("status") != "orphaned_alive":
                    continue
                if _is_primary_row(row):
                    continue
                if attached(rid):
                    continue

                port = row.get("port")
                if not isinstance(port, int):
                    continue

                port_listening = listen(port)
                chrome_pid = row.get("chrome_pid")
                chrome_dead = isinstance(chrome_pid, int) and not alive(chrome_pid)

                if not port_listening or chrome_dead:
                    _release_orphaned_row(
                        active,
                        rid,
                        row,
                        ts=ts,
                        trigger="dead",
                        reaped_kind="dead",
                    )
                    reaped.append(rid)
                    continue

                orphaned_at = row.get("orphaned_at")
                if not isinstance(orphaned_at, (int, float)):
                    continue
                if not include_ttl_reap or ts - float(orphaned_at) < ttl:
                    continue

                # Operator-proxy false-death rows keep living CSE glass; TTL kill
                # here would finish what satellite execution TTL started (6893).
                from claude_bundles.operator_proxy_mission import (
                    is_operator_proxy_mission_purpose,
                )

                purpose = row.get("purpose")
                if is_operator_proxy_mission_purpose(
                    purpose if isinstance(purpose, str) else None
                ):
                    continue

                if kill_listener is not None:
                    with contextlib.suppress(Exception):
                        kill_listener(port)
                _release_orphaned_row(
                    active,
                    rid,
                    row,
                    ts=ts,
                    trigger="ttl",
                    reaped_kind="ttl",
                )
                reaped.append(rid)
    except Exception:
        pass

    return reaped

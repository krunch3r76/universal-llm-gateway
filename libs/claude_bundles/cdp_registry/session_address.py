"""Bind and read CSE chat URLs while maintaining listable registry and orphan projections for durable reattachment."""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from typing import Any

from claude_bundles import cdp_registry_events as _events
from claude_bundles import cdp_registry_store as _store

from .models import (
    _CAPACITY_STATUSES,
    _HOST_LISTABLE_STATUSES,
    Registration,
    _row_to_registration,
    seat_open,
)

_CSE_URL_MARKER = "claude.ai/cowork/cse_"


def _append_lane_less_episode(
    *,
    url: str,
    registration_id: str,
    updated: dict[str, Any],
    execution_id: str | None,
) -> None:
    """Record a host receipt; registry ``parent_thread`` becomes ``lane_thread`` claim."""
    from claude_bundles.cse_provenance import append_episode

    parent_claim = str(updated.get("parent_thread") or "").strip() or None
    append_episode(
        chat_url=url,
        registration_id=registration_id,
        cdp_url=f"http://127.0.0.1:{updated['port']}",
        lane_thread=parent_claim,
        correlation_id=execution_id,
        evidence_class="observed",
    )


def bind_session_address(
    registration_id: str,
    *,
    chat_url: str,
    execution_id: str | None = None,
    target_id: str | None = None,
) -> bool:
    """Persist CSE ``chat_url`` on the registry row (safety property — arc 6885).

    Idempotent: blank *chat_url* is a no-op. Survives ``released`` /
    ``orphaned_retry`` because those transitions copy the row dict.
    Returns True when the row was found and updated (or already matched).
    """
    url = (chat_url or "").strip()
    if not url or "/cowork/cse_" not in url:
        return False
    with _store.ports_lock():
        active = _store.load_active()
        row = active.get(registration_id)
        if row is None:
            return False
        updated = dict(row)
        prior = str(updated.get("chat_url") or "").strip()
        if prior == url and (
            execution_id is None
            or str(updated.get("execution_id") or "") == str(execution_id)
        ):
            _append_lane_less_episode(
                url=url,
                registration_id=registration_id,
                updated=updated,
                execution_id=execution_id,
            )
            return True
        updated["chat_url"] = url
        if execution_id:
            updated["execution_id"] = execution_id
        if target_id:
            updated["target_id"] = target_id
        updated["chat_url_bound_at"] = time.time()
        active[registration_id] = updated
        bound_row, released_rows = apply_driving_seat_bind(active, registration_id)
        _store.write_active(active)
        _store.append_log(
            "session_address_bound",
            {
                "registration_id": registration_id,
                "chat_url": url,
                "execution_id": execution_id,
                "target_id": target_id,
            },
        )
        _append_lane_less_episode(
            url=url,
            registration_id=registration_id,
            updated=active[registration_id],
            execution_id=execution_id,
        )
    _emit_seat_axis_events(bound_row, released_rows)
    return True


def apply_driving_seat_bind(
    active: dict[str, dict[str, Any]],
    registration_id: str,
    *,
    now: float | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Mutate *active* to bind a driving-operator seat and close predecessors.

    No-op when the row is not a driving operator (purpose not in
    ``OPERATOR_PURPOSES`` or ``mission_kind == hop``) or ``parent_thread``
    is empty. Caller must hold ``ports_lock``.
    """
    from claude_bundles.what_is_running_view import OPERATOR_PURPOSES

    row = active.get(registration_id)
    if not isinstance(row, dict):
        return None, []
    purpose = str(row.get("purpose") or "").strip()
    kind = str(row.get("mission_kind") or "root").strip().lower() or "root"
    lane = str(row.get("parent_thread") or "").strip()
    if not lane or purpose not in OPERATOR_PURPOSES or kind == "hop":
        return None, []
    ts = time.time() if now is None else now
    newly_bound = not seat_open(row, lane)
    updated = dict(row)
    updated["seat_lane"] = lane
    updated["seat_closed_at"] = None
    if newly_bound:
        updated["seat_bound_at"] = ts
    active[registration_id] = updated
    released: list[dict[str, Any]] = []
    for other_id, other in list(active.items()):
        if other_id == registration_id or not isinstance(other, dict):
            continue
        if seat_open(other, lane):
            closed = dict(other)
            closed["seat_closed_at"] = ts
            closed["seat_close_reason"] = "superseded"
            closed["superseded_by"] = registration_id
            active[other_id] = closed
            released.append(closed)
    if not newly_bound and not released:
        return None, []
    return updated, released


def bind_driving_seat(registration_id: str) -> None:
    """Bind the driving-operator seat for *registration_id* under ``ports_lock``.

    No-op when the row is not a driving operator or ``parent_thread`` is empty.
    """
    bound_row: dict[str, Any] | None = None
    released_rows: list[dict[str, Any]] = []
    with _store.ports_lock():
        active = _store.load_active()
        bound_row, released_rows = apply_driving_seat_bind(active, registration_id)
        if bound_row is not None or released_rows:
            _store.write_active(active)
            _store.append_log(
                "seat_lane_bound",
                {
                    "registration_id": registration_id,
                    "seat_lane": (bound_row or {}).get("seat_lane"),
                    "superseded": [r.get("registration_id") for r in released_rows],
                },
            )
    _emit_seat_axis_events(bound_row, released_rows)


def _emit_seat_axis_events(
    bound_row: dict[str, Any] | None,
    released_rows: list[dict[str, Any]],
) -> None:
    if bound_row is None and not released_rows:
        return
    lane = str((bound_row or (released_rows[0] if released_rows else {})).get("seat_lane") or "")
    if bound_row is not None:
        superseded = (
            str(released_rows[0].get("registration_id") or "") if released_rows else None
        )
        with contextlib.suppress(Exception):
            _events.emit(
                _events.cdp_seat_lane_bound(
                    registration_id=str(bound_row.get("registration_id") or ""),
                    seat_lane=lane,
                    superseded_registration_id=superseded or None,
                )
            )
    for closed in released_rows:
        with contextlib.suppress(Exception):
            _events.emit(
                _events.cdp_seat_lane_released(
                    registration_id=str(closed.get("registration_id") or ""),
                    seat_lane=str(closed.get("seat_lane") or lane),
                    reason=str(closed.get("seat_close_reason") or "superseded"),
                )
            )


def chat_url_for_registration(registration_id: str | None) -> str | None:
    """Return the durable CSE chat URL for a registration, or None when absent."""
    rid = (registration_id or "").strip()
    if not rid:
        return None
    active = _store.load_active()
    row = active.get(rid)
    if not isinstance(row, dict):
        return None
    url = str(row.get("chat_url") or "").strip()
    return url or None


def list_active() -> list[Registration]:
    """Return active, orphaned-alive, and intentionally retained lanes that remain visible to consumers."""
    active = _store.load_active()
    out = [
        _row_to_registration(row)
        for row in active.values()
        if row.get("status") in _HOST_LISTABLE_STATUSES
    ]
    return sorted(out, key=lambda r: r.port)


def list_capacity() -> list[Registration]:
    """Registry Chrome hosts that consume host-port capacity.

    Occupancy axis (``_CAPACITY_STATUSES``), not lifecycle: ``active`` plus
    ``retained`` (Chrome still reserved after kill=False / hygiene keep).
    ``dormant`` is excluded — no process. ``orphaned_alive`` stays excluded
    (existing seating contract). Registry-only — no Chrome probe — so a
    wedged CDP list cannot hide a protected seat.
    """
    active = _store.load_active()
    out = [
        _row_to_registration(row)
        for row in active.values()
        if row.get("status") in _CAPACITY_STATUSES
    ]
    return sorted(out, key=lambda r: r.port)


def count_capacity_lanes() -> int:
    """Count registry Chrome hosts that occupy the scarce host-port capacity."""
    return len(list_capacity())


def _default_probe_page_urls(port: int) -> list[str]:
    """Best-effort CDP ``/json/list`` scrape for backfill (never raises)."""
    from claude_bundles import cdp_orphans

    payload = cdp_orphans._fetch_json(f"http://127.0.0.1:{port}/json/list")
    return list(cdp_orphans._page_urls_from_list(payload))


def backfill_orphaned_retry_chat_urls(
    *,
    dry_run: bool = True,
    probe_urls: Callable[[int], list[str]] | None = None,
) -> dict[str, Any]:
    """Classify + optionally bind ``chat_url`` for ``orphaned_retry`` rows.

    Verdict classes (arc 6885 / census 6893):
    - ``scrape_bound`` / ``scrape_recoverable``: live ``/cowork/cse_`` on port
    - ``already_bound``: row already carries chat_url
    - ``irreversible_no_url``: Chrome alive or not, no CSE URL on port and none
      recorded — genuine irreversible population unless URL found elsewhere
    """
    probe = probe_urls or _default_probe_page_urls
    active = _store.load_active()
    classes: dict[str, list[dict[str, Any]]] = {
        "already_bound": [],
        "scrape_recoverable": [],
        "scrape_bound": [],
        "irreversible_no_url": [],
    }
    for rid, row in active.items():
        if row.get("status") != "orphaned_retry":
            continue
        entry = {
            "registration_id": rid,
            "port": row.get("port"),
            "prior_chat_url": row.get("chat_url"),
        }
        prior = str(row.get("chat_url") or "").strip()
        if prior and _CSE_URL_MARKER in prior:
            classes["already_bound"].append(entry)
            continue
        port = row.get("port")
        urls: list[str] = []
        if isinstance(port, int):
            with contextlib.suppress(Exception):
                urls = [u for u in probe(port) if _CSE_URL_MARKER in str(u)]
        if urls:
            entry["scraped_chat_url"] = urls[0]
            if dry_run:
                classes["scrape_recoverable"].append(entry)
            elif bind_session_address(rid, chat_url=urls[0]):
                classes["scrape_bound"].append(entry)
            else:
                classes["scrape_recoverable"].append(entry)
        else:
            classes["irreversible_no_url"].append(entry)
    return {
        "dry_run": dry_run,
        "counts": {k: len(v) for k, v in classes.items()},
        "rows": classes,
    }


def log_orphan_scan(scan: Any) -> None:
    """Emit orphan-scan observation event on every scan.

    ``closable`` / ``protected`` classifications are **scan-ephemeral** (S1/S2):
    they appear in ``orphan_scan_as_dict`` output and are not persisted on
    registry rows. S3 ``cdp_lane_reaper`` consumes fresh scan dicts when reclaim
    is flag-enabled; default reclaim remains OFF (AC4).
    """
    closable = sum(
        1
        for orphan in scan.matched
        for target in getattr(orphan, "cse_targets", ())
        if getattr(target, "classification", None) == "closable"
    )
    protected = sum(
        1
        for orphan in scan.matched
        for target in getattr(orphan, "cse_targets", ())
        if getattr(target, "classification", None) == "protected"
    )
    _events.emit(
        _events.cdp_port_orphan_scan(
            ports_live=scan.ports_live,
            ports_skipped_registered=scan.ports_skipped_registered,
            ports_examined=scan.ports_examined,
            matched_count=len(scan.matched),
            rejected_count=len(scan.rejected),
            unevaluable_count=len(scan.unevaluable),
            closable_count=closable,
            protected_count=protected,
        )
    )


def _load_active() -> dict[str, dict[str, Any]]:
    return _store.load_active()

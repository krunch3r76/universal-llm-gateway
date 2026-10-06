"""Bind and read CSE chat URLs while maintaining listable registry and orphan projections for durable reattachment.

Rows come from ``cdp_registry_store.ACTIVE_JSON`` on this host. That file is
not the Jupiter registry; the pin is ``REGISTRATION_REGISTRY_SSH_ENV``
(agent-bus:13001).
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Callable
from typing import Any

from universal_protocol.errors import ProtocolError

from claude_bundles import cdp_registry_events as _events
from claude_bundles import cdp_registry_store as _store
from claude_bundles.cse_url import normalize_cse_url

from .attachment_journal import (
    append_attachment_journal,
    assert_attachment_unique,
    fold_attachment_journal,
    has_attachment_observed,
    host_listable_ids_for_norm_url,
)
from .execution_state import row_execution_in_flight
from .models import (
    _CAPACITY_STATUSES,
    _HOST_LISTABLE_STATUSES,
    Registration,
    _row_to_registration,
    seat_open,
)

logger = logging.getLogger(__name__)

_CSE_URL_MARKER = "claude.ai/cowork/cse_"


def attachment_for_chat_url(chat_url: str) -> Registration | None:
    """Return the sole host-listable row for *chat_url*, or None."""
    norm = normalize_cse_url(chat_url or "")
    if not norm:
        return None
    active = _store.load_active()
    matches = host_listable_ids_for_norm_url(active, norm)
    if len(matches) > 1:
        raise ProtocolError(
            code="attachment.conflict",
            message=f"multiple host-listable rows for chat_url: {matches!r}",
            source="rpc",
            retryable=False,
            data={"chat_url": chat_url, "registration_ids": matches},
        )
    if not matches:
        return None
    row = active.get(matches[0])
    if not isinstance(row, dict):
        return None
    return _row_to_registration(row)


def backfill_attachment_from_chat_url(
    registration_id: str,
    *,
    chat_url: str,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Migration helper: journal ``backfill_url_bound`` without seat changes."""
    url = (chat_url or "").strip()
    if not url or _CSE_URL_MARKER not in url:
        return {"dry_run": dry_run, "skipped": "not_cse_url"}
    if dry_run:
        return {"dry_run": True, "would_bind": registration_id, "chat_url": url}
    if has_attachment_observed(registration_id, url):
        fold_attachment_journal()
        return {
            "dry_run": False,
            "registration_id": registration_id,
            "idempotent": True,
        }
    append_attachment_journal(
        registration_id=registration_id,
        chat_url=url,
        attach_proof="backfill_url_bound",
    )
    fold_attachment_journal()
    return {"dry_run": False, "registration_id": registration_id, "bound": True}


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


def bind_chat_url_only(
    registration_id: str,
    *,
    chat_url: str,
    execution_id: str | None = None,
    target_id: str | None = None,
) -> bool:
    """Persist CSE ``chat_url`` without seating or holder upsert.

    Reattach uses this until ``commit_reattach_driving_seat`` after the second
    seat gate and lane acquire. The idempotent re-bind branch does not upsert.
    """
    return bind_session_address(
        registration_id,
        chat_url=chat_url,
        execution_id=execution_id,
        target_id=target_id,
        defer_seat_commit=True,
    )


def bind_session_address(
    registration_id: str,
    *,
    chat_url: str,
    execution_id: str | None = None,
    target_id: str | None = None,
    defer_seat_commit: bool = False,
) -> bool:
    """Persist CSE ``chat_url`` on the registry row (safety property — arc 6885).

    Idempotent: blank *chat_url* is a no-op. Survives ``released`` /
    ``orphaned_retry`` because those transitions copy the row dict.
    Returns True when the row was found and updated (or already matched).

    ``defer_seat_commit`` writes the URL only. Seat bind and holder upsert wait
    for ``commit_reattach_driving_seat``.
    """
    url = (chat_url or "").strip()
    if not url or "/cowork/cse_" not in url:
        return False
    bound_row: dict[str, Any] | None = None
    released_rows: list[dict[str, Any]] = []
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
            if not defer_seat_commit:
                from services.git_integration_worker.cse_session_holders import (
                    upsert_holder_remote,
                )

                upsert_holder_remote(
                    chat_url=url,
                    registration_id=registration_id,
                    execution_id=execution_id,
                )
            return True
        if prior and row_execution_in_flight(row):
            # A seat driving a live execution is addressed by that execution's
            # session; an observed foreign CSE URL (a restored tab, a redirect)
            # must not re-point the row — the resume path follows chat_url
            # (a:36948 acceptance: probe row rebound to the operator seat).
            _store.append_log(
                "session_address_rebind_refused",
                {
                    "registration_id": registration_id,
                    "chat_url": prior,
                    "refused_chat_url": url,
                    "execution_id": execution_id,
                },
            )
            return False
        assert_attachment_unique(active, url, registration_id=registration_id)
        updated["chat_url"] = url
        if execution_id:
            updated["execution_id"] = execution_id
        if target_id:
            updated["target_id"] = target_id
        updated["chat_url_bound_at"] = time.time()
        active[registration_id] = updated
        _store.write_active(active)
        if not defer_seat_commit:
            bound_row, released_rows = apply_driving_seat_bind(active, registration_id)
            if bound_row is not None or released_rows:
                _store.require_seat_authority(operation="bind_session_address")
                _store.write_active(active)
                _store.append_seat_transition_journal(
                    registration_id=registration_id,
                    seat_lane=str((bound_row or {}).get("seat_lane") or ""),
                    seat_bound_at=(bound_row or {}).get("seat_bound_at"),
                    superseded=[
                        str(r.get("registration_id") or "")
                        for r in released_rows
                        if r.get("registration_id")
                    ],
                )
        _store.append_log(
            "session_address_bound",
            {
                "registration_id": registration_id,
                "chat_url": url,
                "execution_id": execution_id,
                "target_id": target_id,
                "defer_seat_commit": defer_seat_commit,
            },
        )
        _append_lane_less_episode(
            url=url,
            registration_id=registration_id,
            updated=active[registration_id],
            execution_id=execution_id,
        )
    if defer_seat_commit:
        return True
    _emit_seat_axis_events(bound_row, released_rows)
    from services.git_integration_worker.cse_session_holders import upsert_holder_remote

    upsert_holder_remote(
        chat_url=url,
        registration_id=registration_id,
        execution_id=execution_id,
    )
    return True


def restore_session_address(
    registration_id: str,
    *,
    expected_chat_url: str,
    restore_to: str | None,
) -> str:
    """Atomically revert or clear a borrow bind under ``ports_lock``.

    Does not call ``apply_driving_seat_bind`` or ``upsert_holder_remote``.
    """
    rid = (registration_id or "").strip()
    expected_norm = normalize_cse_url(expected_chat_url or "")
    if not rid or not expected_norm:
        return "missing"
    with _store.ports_lock():
        active = _store.load_active()
        row = active.get(rid)
        if not isinstance(row, dict):
            return "missing_row"
        current = str(row.get("chat_url") or "").strip()
        if normalize_cse_url(current) != expected_norm:
            return "rebound"
        if row_execution_in_flight(row):
            return "in_flight"
        active = _store.load_active()
        row = active.get(rid)
        if not isinstance(row, dict):
            return "missing_row"
        current = str(row.get("chat_url") or "").strip()
        if normalize_cse_url(current) != expected_norm:
            return "rebound"
        updated = dict(row)
        target_restore = (restore_to or "").strip() or None
        if target_restore:
            try:
                assert_attachment_unique(
                    active, target_restore, registration_id=rid
                )
            except ProtocolError:
                target_restore = None
        if target_restore:
            updated["chat_url"] = target_restore
            updated["chat_url_bound_at"] = time.time()
        else:
            updated.pop("chat_url", None)
            updated.pop("chat_url_bound_at", None)
        active[rid] = updated
        _store.write_active(active)
        _store.append_log(
            "session_address_restored",
            {
                "registration_id": rid,
                "expected_chat_url": expected_chat_url,
                "restore_to": target_restore,
                "prior_chat_url": current,
            },
        )
    return "restored"


def _open_seat_holder(
    active: dict[str, dict[str, Any]], lane: str
) -> tuple[str | None, float | None]:
    """Open seat on *lane* with the greatest ``seat_bound_at``, else vacant."""
    lane_key = (lane or "").strip()
    if not lane_key:
        return None, None
    best_id: str | None = None
    best_ts: float | None = None
    for rid, row in active.items():
        if not isinstance(row, dict) or not seat_open(row, lane_key):
            continue
        raw = row.get("seat_bound_at")
        try:
            ts = float(raw) if raw is not None else 0.0
        except (TypeError, ValueError):
            ts = 0.0
        if best_ts is None or ts > best_ts:
            best_id = str(row.get("registration_id") or rid)
            best_ts = ts
    return best_id, best_ts


def open_lane_seat_holder(lane: str) -> tuple[str | None, float | None]:
    """Read the lane's open seat holder ``(registration_id, seat_bound_at)``."""
    return _open_seat_holder(_store.load_active(), lane)


def commit_reattach_driving_seat(
    registration_id: str,
    *,
    chat_url: str,
    lane: str,
    snapshot_holder_id: str | None,
    snapshot_seat_bound_at: float | None,
    execution_id: str | None = None,
) -> str:
    """Seat and upsert after reattach, or refuse when the lane seat moved.

    Under ``ports_lock``, the open holder ``(registration_id, seat_bound_at)``
    must still match the reattach snapshot.     A mismatch returns ``refused``
    without seating or upserting. A missing registry row returns ``committed``
    without writing: the chat_url bind did not persist a row. When
    ``apply_driving_seat_bind`` no-ops (closed seat, empty lane, or non-operator
    purpose) the result is ``not_seated`` and the holder is not upserted.
    Otherwise the seat is written, then best-effort ``upsert_holder_remote``.
    """
    rid = (registration_id or "").strip()
    url = (chat_url or "").strip()
    lane_key = (lane or "").strip()
    if not rid or not url:
        return "missing"
    bound_row: dict[str, Any] | None = None
    released_rows: list[dict[str, Any]] = []
    with _store.ports_lock():
        active = _store.load_active()
        row = active.get(rid)
        if not isinstance(row, dict):
            return "committed"
        if lane_key:
            current_id, current_bound = _open_seat_holder(active, lane_key)
            if (current_id, current_bound) != (
                snapshot_holder_id,
                snapshot_seat_bound_at,
            ):
                return "refused"
        bound_row, released_rows = apply_driving_seat_bind(active, rid)
        if bound_row is None:
            return "not_seated"
        _store.require_seat_authority(operation="commit_reattach_driving_seat")
        _store.write_active(active)
        _store.append_seat_transition_journal(
            registration_id=rid,
            seat_lane=str(bound_row.get("seat_lane") or ""),
            seat_bound_at=bound_row.get("seat_bound_at"),
            superseded=[
                str(r.get("registration_id") or "")
                for r in released_rows
                if r.get("registration_id")
            ],
        )
    _emit_seat_axis_events(bound_row, released_rows)
    from services.git_integration_worker.cse_session_holders import upsert_holder_remote

    try:
        upsert_holder_remote(
            chat_url=url,
            registration_id=rid,
            execution_id=execution_id,
        )
    except Exception:
        logger.exception(
            "commit_reattach_driving_seat upsert failed reg=%s; seat already written",
            rid,
        )
    return "committed"


def _next_seat_bound_at(ts: float, released: list[dict[str, Any]]) -> float:
    """Monotonic seat provenance: strictly after every predecessor closed in this bind."""
    floor = ts
    for row in released:
        prior = row.get("seat_bound_at")
        if prior is None:
            continue
        try:
            floor = max(floor, float(prior) + 1e-6)
        except (TypeError, ValueError):
            continue
    if released and floor <= ts:
        return ts + 1e-6
    return floor


def apply_driving_seat_bind(
    active: dict[str, dict[str, Any]],
    registration_id: str,
    *,
    now: float | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Mutate *active* to bind a driving-operator seat and close predecessors.

    No-op when purpose is outside ``OPERATOR_PURPOSES`` or ``parent_thread``
    is empty. Hop successors with an operator purpose bind this seat.
    Caller must hold ``ports_lock``.
    """
    from claude_bundles.what_is_running_view import OPERATOR_PURPOSES

    row = active.get(registration_id)
    if not isinstance(row, dict):
        return None, []
    purpose = str(row.get("purpose") or "").strip()
    lane = str(row.get("parent_thread") or "").strip()
    if not lane or purpose not in OPERATOR_PURPOSES:
        return None, []
    if row.get("seat_closed_at") is not None:
        return None, []
    ts = time.time() if now is None else now
    updated = dict(row)
    updated["seat_lane"] = lane
    updated["seat_closed_at"] = None
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
    updated["seat_bound_at"] = _next_seat_bound_at(ts, released)
    active[registration_id] = updated
    return updated, released


def retire_predecessor_identity(
    registration_id: str,
    *,
    parent_thread: str | None = None,
) -> list[str]:
    """Close every other registry row on this lane, including never-seat-open rows.

    Census drops a row once ``seat_closed_at`` is set. A successor that is
    only a birth id (not yet in the active map) still closes lane peers and
    records ``superseded_by`` as that birth id. No-op when the lane is empty
    or no open peer remains.
    """
    rid = (registration_id or "").strip()
    if not rid:
        return []
    released: list[str] = []
    bound_row: dict[str, Any] | None = None
    released_rows: list[dict[str, Any]] = []
    with _store.ports_lock():
        active = _store.load_active()
        row = active.get(rid)
        row_ok = isinstance(row, dict)
        lane = (
            parent_thread or (str(row.get("parent_thread") or "") if row_ok else "")
        ).strip()
        if not lane:
            return []
        ts = time.time()
        for other_id, other in list(active.items()):
            if other_id == rid or not isinstance(other, dict):
                continue
            if other.get("seat_closed_at") is not None:
                continue
            parent = str(other.get("parent_thread") or "").strip()
            # parent_thread keeps the never-seat-open close. seat_open covers
            # a seat-lane row whose parent_thread was never copied.
            if parent != lane and not seat_open(other, lane):
                continue
            closed = dict(other)
            closed["seat_closed_at"] = ts
            closed["seat_close_reason"] = "superseded"
            closed["superseded_by"] = rid
            active[other_id] = closed
            released.append(str(other_id))
            released_rows.append(closed)
        if row_ok:
            updated = dict(row)
            updated["parent_thread"] = lane
            updated["seat_lane"] = lane
            updated["seat_closed_at"] = None
            updated["seat_bound_at"] = ts
            active[rid] = updated
            bound_row = updated
        elif not released:
            return []
        _store.require_seat_authority(operation="retire_predecessor_identity")
        _store.write_active(active)
        _store.append_seat_transition_journal(
            registration_id=rid,
            seat_lane=lane,
            seat_bound_at=ts if row_ok else None,
            superseded=released,
        )
    _emit_seat_axis_events(bound_row, released_rows)
    return released


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
            _store.require_seat_authority(operation="bind_driving_seat")
            _store.write_active(active)
            _store.append_seat_transition_journal(
                registration_id=registration_id,
                seat_lane=str((bound_row or {}).get("seat_lane") or ""),
                seat_bound_at=(bound_row or {}).get("seat_bound_at"),
                superseded=[
                    str(r.get("registration_id") or "")
                    for r in released_rows
                    if r.get("registration_id")
                ],
            )
    _emit_seat_axis_events(bound_row, released_rows)


def _emit_seat_axis_events(
    bound_row: dict[str, Any] | None,
    released_rows: list[dict[str, Any]],
) -> None:
    if bound_row is None and not released_rows:
        return
    lane = str(
        (bound_row or (released_rows[0] if released_rows else {})).get("seat_lane")
        or ""
    )
    if bound_row is not None:
        superseded = (
            str(released_rows[0].get("registration_id") or "")
            if released_rows
            else None
        )
        with contextlib.suppress(Exception):
            _events.emit(
                _events.cdp_seat_lane_bound(
                    registration_id=str(bound_row.get("registration_id") or ""),
                    seat_lane=lane,
                    superseded_registration_id=superseded or None,
                )
            )
        if len(released_rows) >= 2:
            with contextlib.suppress(Exception):
                _events.emit(
                    _events.cdp_seat_lane_reconciled(
                        registration_id=str(bound_row.get("registration_id") or ""),
                        seat_lane=lane,
                        released_registration_ids=[
                            str(r.get("registration_id") or "")
                            for r in released_rows
                            if r.get("registration_id")
                        ],
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

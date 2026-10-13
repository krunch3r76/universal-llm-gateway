"""Seat-fireable cap-stop clear — manage ``charter_cap_stop_clear``."""

from __future__ import annotations

from typing import Any

from universal_logging import get_logger

from libs.charter_runner_store.db import charter_runner_data_dir

from .admission.caps import CapStore

logger = get_logger(__name__)

# Reasons ``test_charter_runner.py`` writes via ``CapStore.mark_failed``.
SUITE_CLEARABLE_STOP_REASONS = frozenset({"worker_failed", "stale_window"})

_EVENT_LOOKBACK_MINUTES = 24 * 60


def _stop_reason_on_disk(root_id: str) -> str | None:
    path = charter_runner_data_dir() / "cap-stops" / f"{root_id}.json"
    if not path.is_file():
        return None
    raw = CapStore._read_stop_file(path)
    if raw is None:
        return None
    reason = raw.get("stopped_reason")
    return reason if isinstance(reason, str) and reason.strip() else None


def _recent_substrate_events_for_root(root_id: str) -> tuple[list[dict], list[dict]]:
    """Return (tick_errors, window_failed) naming ``root_id`` in the last 24h."""
    try:
        from scripts.model_manager.ui.dispatch_monitor.ulg.event_query import (
            signal_events,
        )
    except ImportError:
        return [], []

    errors: list[dict] = []
    failed: list[dict] = []
    for signal, bucket in (
        ("manage.charter.tick.error", errors),
        ("manage.charter.tick.window_failed", failed),
    ):
        try:
            rows = signal_events(
                signal,
                minutes=_EVENT_LOOKBACK_MINUTES,
                limit=500,
                timeout=5.0,
            )
        except Exception as exc:  # noqa: BLE001 — event service optional at clear time
            logger.warning(
                "cap_stop_clear event query failed signal=%s: %s", signal, exc
            )
            rows = []
        for row in rows:
            payload = row.get("payload") if isinstance(row, dict) else None
            if not isinstance(payload, dict):
                continue
            subject = str(row.get("subject") or payload.get("root") or "").strip()
            if subject == root_id:
                bucket.append(row)
    return errors, failed


async def cap_stop_clear(
    root_id: str,
    *,
    set_by: str = "manage",
    force: bool = False,
) -> dict[str, Any]:
    """Clear a durable cap stop when substrate + reason gates pass."""
    rid = root_id.strip()
    if not rid:
        raise ValueError("root_id required")

    reason = _stop_reason_on_disk(rid)
    if reason is None:
        store = CapStore()
        allowed, cap_reason = store.check(rid)
        if allowed:
            return {
                "root_id": rid,
                "cleared": False,
                "already_clear": True,
                "set_by": set_by,
            }
        reason = (cap_reason or "").removeprefix("stopped:") or None

    tick_errors, window_failed = _recent_substrate_events_for_root(rid)
    if not force and (tick_errors or window_failed):
        return {
            "root_id": rid,
            "cleared": False,
            "reason": "substrate_events_in_24h",
            "stopped_reason": reason,
            "manage.charter.tick.error_count": len(tick_errors),
            "manage.charter.tick.window_failed_count": len(window_failed),
            "set_by": set_by,
        }

    if reason not in SUITE_CLEARABLE_STOP_REASONS and not force:
        return {
            "root_id": rid,
            "cleared": False,
            "reason": "stop_reason_not_suite_clearable",
            "stopped_reason": reason,
            "clearable_reasons": sorted(SUITE_CLEARABLE_STOP_REASONS),
            "set_by": set_by,
        }

    store = CapStore()
    had = store.reset(rid)
    allowed_after, cap_after = store.check(rid)
    return {
        "root_id": rid,
        "cleared": had,
        "had_stop": had,
        "stopped_reason_before": reason,
        "admission_allowed_after": allowed_after,
        "cap_skip_after": cap_after,
        "set_by": set_by,
        "force": force,
    }


__all__ = ["SUITE_CLEARABLE_STOP_REASONS", "cap_stop_clear"]

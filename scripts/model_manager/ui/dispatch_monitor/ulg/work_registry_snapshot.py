"""Live work-registry fetch for dispatch-board holder paint (view overlay)."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from scripts.model_manager.ui.dispatch_monitor.core.dtos import WorkRegistryRow
from scripts.model_manager.ui.dispatch_monitor.ulg.lease_snapshot import worker_url

_PATH = "/api/v1/cursor/registry"
_TIMEOUT_S = 3.0
_MAX_ATTEMPTS = 2
_BACKOFF_S = 0.15


def fetch_work_registry_live(
    *,
    base_url: str | None = None,
    get_json: Callable[[str], Mapping[str, Any]] | None = None,
) -> tuple[WorkRegistryRow, ...]:
    """GET live registry rows; empty tuple on failure (board keeps last frame)."""
    target = f"{worker_url(base_url)}{_PATH}?state=live&window_hours=24"
    body: Mapping[str, Any] | None
    if get_json is not None:
        try:
            raw = get_json(target)
            body = raw if isinstance(raw, Mapping) else None
        except Exception:
            body = None
    else:
        body = _http_get_json(target)
    if body is None:
        return ()
    entries = body.get("entries")
    if not isinstance(entries, list):
        return ()
    rows: list[WorkRegistryRow] = []
    for item in entries:
        if not isinstance(item, Mapping):
            continue
        row_id = str(item.get("id") or "").strip()
        if not row_id:
            continue
        work_key = str(item.get("work_key") or "").strip()
        display = str(item.get("work_key_display") or work_key).strip()
        seat = item.get("seat_name")
        seat_name = str(seat).strip() if isinstance(seat, str) and seat.strip() else None
        caller = item.get("caller_agent")
        caller_agent = (
            str(caller).strip() if isinstance(caller, str) and caller.strip() else None
        )
        thread = item.get("thread_id")
        thread_id = str(thread).strip() if thread is not None and str(thread).strip() else None
        rows.append(
            WorkRegistryRow(
                row_id=row_id,
                kind=str(item.get("kind") or "dispatch"),
                status=str(item.get("status") or "?"),
                work_key=work_key,
                work_key_display=display or work_key,
                thread_id=thread_id,
                caller_agent=caller_agent,
                seat_name=seat_name,
            )
        )
    return tuple(rows)


def registry_fingerprint(rows: tuple[WorkRegistryRow, ...]) -> tuple[Any, ...]:
    """Cheap change detector for controller publish."""
    return tuple(
        (r.row_id, r.status, r.work_key, r.seat_name, r.caller_agent) for r in rows
    )


def _http_get_json(url: str) -> dict[str, Any] | None:
    last_error: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            with httpx.Client(timeout=_TIMEOUT_S) as client:
                response = client.get(url)
                response.raise_for_status()
                body = response.json()
                return body if isinstance(body, dict) else None
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt + 1 < _MAX_ATTEMPTS:
                time.sleep(_BACKOFF_S * (attempt + 1))
    _ = last_error
    return None


def registry_poll_interval_s() -> float:
    raw = os.environ.get("DISPATCH_BOARD_REGISTRY_POLL_S", "5")
    try:
        return max(2.0, float(raw))
    except ValueError:
        return 5.0

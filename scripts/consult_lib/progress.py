"""Event-driven client liveness for non-streaming Stargate consult POSTs.

Replaces a monolithic httpx read timeout with:
  - granular connect/write/pool timeouts (read unbounded)
  - a hard wall-clock deadline
  - an out-of-band admission/status poller that resets a no-progress
    watchdog on forward load transitions

Primary surface: ``GET /api/v1/admission/state?model_id=``
Fallback: ``GET /v1/models/{id}?include_status=true`` via readiness probe.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Any

import httpx

from .readiness import probe_model_status

DEFAULT_MIN_DEADLINE_S = 900.0
DEFAULT_POLL_INTERVAL_S = 5.0
CONNECT_TIMEOUT_S = 10.0
WRITE_TIMEOUT_S = 30.0
POOL_TIMEOUT_S = 10.0
PROBE_TIMEOUT_S = 5.0

_STATUS_RANK = {
    "available": 0,
    "loading": 1,
    "loaded": 2,
    "busy": 2,
}


class ProgressAbortError(TimeoutError):
    """Raised when the hard deadline or no-progress watchdog aborts a POST."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        msg = f"progress abort ({reason})"
        if detail:
            msg = f"{msg}: {detail}"
        super().__init__(msg)


def derive_deadline(step_budget: float, deadline: float | None = None) -> float:
    """Hard ceiling: explicit deadline, else max(step_budget, 900s)."""
    if deadline is not None:
        return float(deadline)
    return max(float(step_budget), DEFAULT_MIN_DEADLINE_S)


def _probe_admission(stargate_url: str, model_id: str) -> dict[str, Any] | None:
    url = f"{stargate_url.rstrip('/')}/api/v1/admission/state"
    try:
        with httpx.Client(timeout=PROBE_TIMEOUT_S) as client:
            resp = client.get(url, params={"model_id": model_id})
        if resp.status_code >= 400:
            return None
        data = resp.json()
        return data if isinstance(data, dict) else None
    except (httpx.HTTPError, ValueError, TypeError):
        return None


def _snapshot(
    stargate_url: str, model_id: str
) -> dict[str, Any] | None:
    """Admission snapshot, or status-only fallback. None = probe failed (neutral)."""
    admission = _probe_admission(stargate_url, model_id)
    if admission is not None:
        return {
            "source": "admission",
            "queue_depth": int(admission.get("queue_depth") or 0),
            "loading": bool(admission.get("loading")),
            "loaded": bool(admission.get("loaded")),
            "paused": bool(admission.get("paused")),
            "status": None,
        }
    status = probe_model_status(
        model_id, stargate_url, timeout=PROBE_TIMEOUT_S
    )
    if status is None:
        return None
    return {
        "source": "status",
        "queue_depth": 0,
        "loading": status == "loading",
        "loaded": status in ("loaded", "busy"),
        "paused": False,
        "status": status,
    }


def _is_forward(prev: dict[str, Any] | None, curr: dict[str, Any]) -> bool:
    if prev is None:
        return False
    if curr["queue_depth"] < prev["queue_depth"]:
        return True
    if curr["loading"] and not prev["loading"]:
        return True
    if curr["loaded"] and not prev["loaded"]:
        return True
    prev_status = prev.get("status")
    curr_status = curr.get("status")
    if (
        isinstance(prev_status, str)
        and isinstance(curr_status, str)
        and _STATUS_RANK.get(curr_status, -1) > _STATUS_RANK.get(prev_status, -1)
    ):
        return True
    return False


def _active_load(snap: dict[str, Any]) -> bool:
    """Queued or loading ⇒ treat as pending work (refresh watchdog each poll)."""
    return bool(snap["loading"] or snap["queue_depth"] > 0)


def post_with_progress(
    url: str,
    body: dict[str, Any],
    *,
    model_id: str,
    stargate_url: str,
    step_budget: float,
    deadline: float | None = None,
    poll_interval: float = DEFAULT_POLL_INTERVAL_S,
    params: dict[str, Any] | None = None,
) -> httpx.Response:
    """POST with out-of-band progress polling and a hard deadline.

    ``step_budget`` is the no-progress budget (CLI ``--timeout``).
    ``deadline`` is the absolute wall-clock ceiling (CLI ``--deadline``).
    """
    hard_deadline_s = derive_deadline(step_budget, deadline)
    started = time.monotonic()
    deadline_at = started + hard_deadline_s
    last_progress = started
    last_snap: dict[str, Any] | None = None
    stop = threading.Event()
    abort_reason: list[str] = []
    client_holder: list[httpx.Client] = []

    def _close_client() -> None:
        if client_holder:
            try:
                client_holder[0].close()
            except Exception:
                pass

    def _poller() -> None:
        nonlocal last_progress, last_snap
        while not stop.wait(poll_interval):
            now = time.monotonic()
            if now >= deadline_at:
                abort_reason.append("hard_deadline")
                _close_client()
                return
            snap = _snapshot(stargate_url, model_id)
            if snap is None:
                continue
            if _is_forward(last_snap, snap) or _active_load(snap):
                last_progress = now
                print(
                    f"  progress: model={model_id} "
                    f"loading={snap['loading']} loaded={snap['loaded']} "
                    f"queue={snap['queue_depth']} "
                    f"source={snap['source']}",
                    file=sys.stderr,
                )
            last_snap = snap
            if (
                not _active_load(snap)
                and (now - last_progress) > step_budget
            ):
                abort_reason.append("no_progress")
                _close_client()
                return

    timeout = httpx.Timeout(
        connect=CONNECT_TIMEOUT_S,
        write=WRITE_TIMEOUT_S,
        pool=POOL_TIMEOUT_S,
        read=None,
    )
    poller = threading.Thread(target=_poller, name="consult-progress", daemon=True)
    poller.start()
    try:
        with httpx.Client(timeout=timeout) as client:
            client_holder.append(client)
            try:
                return client.post(url, json=body, params=params or {})
            except httpx.RequestError as exc:
                if abort_reason:
                    raise ProgressAbortError(
                        abort_reason[0],
                        detail=(
                            f"model={model_id} after "
                            f"{time.monotonic() - started:.0f}s"
                        ),
                    ) from exc
                raise
    finally:
        stop.set()
        poller.join(timeout=max(poll_interval, 2.0))

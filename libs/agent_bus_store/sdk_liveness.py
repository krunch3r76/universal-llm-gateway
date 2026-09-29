"""SDK dispatch liveness probe for orphan reconcile and admitted-TTL reap.

Callers in ``reconcile`` and ``watchdog._reap_admitted`` ask GIW whether a
thread still has a live holder before treating quiet bus clocks as death.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from .producer_projection import producer_liveness_grace

logger = logging.getLogger("agent-bus.sdk_liveness")

# parked_waiting is a live nest-park state (GIW live_holders keys off the child
# task). Omitting it classifies as DEFER `probe_status_unknown_parked_waiting`
# (thread 9476, 2026-08-18T08:40:37Z) and must not be treated as death.
_LIVE_STATUSES = frozenset({"queued", "admitted", "running", "parked_waiting"})
# GIW ledger also emits ``cancelled`` (park/restart); bus links only store completed|failed.
_WORKER_TERMINAL_TO_BUS = {
    "completed": "completed",
    "failed": "failed",
    "cancelled": "failed",
}
_HEARTBEAT_STALE_S: float = float(os.getenv("AGENT_BUS_SDK_HEARTBEAT_STALE_S", "300"))
_PROBE_TIMEOUT_S: float = float(os.getenv("AGENT_BUS_SDK_PROBE_TIMEOUT_S", "2"))


class LivenessVerdict(StrEnum):
    """Holder-probe outcome for GIW dispatch-status: skip live, allow orphan, defer fail-closed, or terminal backfill."""

    SKIP_LIVE = "skip_live"
    ALLOW_ORPHAN = "allow_orphan"
    DEFER = "defer"
    TERMINAL_BACKFILL = "terminal_backfill"


@dataclass(frozen=True, slots=True)
class ProbeResult:
    payload: dict[str, Any] | None
    http_status: int | None
    error: str | None


def _worker_base_url() -> str:
    return os.environ.get("GIT_INTEGRATION_WORKER_URL", "http://127.0.0.1:8091").rstrip(
        "/"
    )


def parse_ts(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp, accepting a trailing Z as UTC."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def heartbeat_freshness(last_heartbeat_at: str | None) -> str:
    """Return ``live``, ``stale``, or ``indeterminate`` for heartbeat age."""
    if last_heartbeat_at is None:
        return "live"
    try:
        hb = parse_ts(last_heartbeat_at)
        if hb.tzinfo is None:
            hb = hb.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return "indeterminate"
    age_s = (datetime.now(UTC) - hb.astimezone(UTC)).total_seconds()
    if age_s < 0:
        return "indeterminate"
    if age_s > _HEARTBEAT_STALE_S:
        return "stale"
    return "live"


def _park_for_restart_holds(payload: dict[str, Any]) -> bool:
    """True when a cancelled row is a live ``park_for_restart``, not its end.

    The resume child keeps the parent's ``execution_id``. ``cancel_discard``
    and ``park.state == expired`` end the execution and stay terminal.
    ``park_kind`` is read from the dispatch-status ``park`` block or the row.
    """
    park = payload.get("park")
    kind = payload.get("park_kind")
    state = None
    if isinstance(park, dict):
        if kind is None:
            kind = park.get("park_kind")
        state = park.get("state")
    if kind != "park_for_restart":
        return False
    return state != "expired"


def classify_probe(
    probe: ProbeResult,
    *,
    link_execution_id: str | None,
    sole_link: bool = True,
) -> tuple[LivenessVerdict, str, str | None]:
    """Classify a GIW dispatch-status probe. Returns (verdict, reason, terminal).

    ``sole_link`` is the 1:1 case (thread 11151): a terminal probe may backfill
    the only link even when the probe's execution_id string differs. When the
    thread has another link, a terminal probe for a different execution_id is
    that sibling — it must not stamp this link.

    A ``cancelled`` probe that ``_park_for_restart_holds`` is not death. The
    resume child is still the execution, so the verdict is ``SKIP_LIVE``.
    Heartbeat age does not change a live status.
    """
    if probe.error is not None:
        return LivenessVerdict.DEFER, probe.error, None

    if probe.http_status == 404:
        return LivenessVerdict.ALLOW_ORPHAN, "probe_not_found", None

    if probe.http_status is not None and probe.http_status >= 400:
        return (
            LivenessVerdict.DEFER,
            f"probe_http_{probe.http_status}",
            None,
        )

    payload = probe.payload
    if payload is None:
        return LivenessVerdict.DEFER, "probe_empty_payload", None

    status = payload.get("status")
    if status is None:
        return LivenessVerdict.ALLOW_ORPHAN, "probe_status_null", None

    if not isinstance(status, str):
        return LivenessVerdict.DEFER, "probe_status_malformed", None

    bus_terminal = _WORKER_TERMINAL_TO_BUS.get(status)
    if bus_terminal is not None:
        if _park_for_restart_holds(payload):
            return LivenessVerdict.SKIP_LIVE, "park_resume_holds_execution", None
        probe_execution_id = payload.get("execution_id")
        execution_id_mismatch = (
            link_execution_id
            and probe_execution_id
            and str(probe_execution_id) != str(link_execution_id)
        )
        if execution_id_mismatch and not sole_link:
            return LivenessVerdict.SKIP_LIVE, "probe_other_execution", None
        return LivenessVerdict.TERMINAL_BACKFILL, "probe_terminal", bus_terminal

    if status not in _LIVE_STATUSES:
        return LivenessVerdict.DEFER, f"probe_status_unknown_{status}", None

    probe_execution_id = payload.get("execution_id")
    execution_id_mismatch = (
        link_execution_id
        and probe_execution_id
        and str(probe_execution_id) != str(link_execution_id)
    )

    if status == "parked_waiting":
        if execution_id_mismatch:
            return LivenessVerdict.ALLOW_ORPHAN, "execution_id_mismatch", None
        # Parent heartbeat can go stale while the nested child is the live
        # holder. GIW live_holders does not require a fresh parent heartbeat.
        return LivenessVerdict.SKIP_LIVE, "worker_live", None

    freshness = heartbeat_freshness(payload.get("last_heartbeat_at"))
    if freshness == "indeterminate":
        return LivenessVerdict.DEFER, "heartbeat_indeterminate", None
    if freshness == "stale":
        return LivenessVerdict.ALLOW_ORPHAN, "heartbeat_stale", None
    if execution_id_mismatch:
        return LivenessVerdict.ALLOW_ORPHAN, "execution_id_mismatch", None
    return LivenessVerdict.SKIP_LIVE, "worker_live", None


def probe_dispatch_status(thread_id: str) -> ProbeResult:
    """HTTP GET dispatch-status for ``thread_id``."""
    query = urllib.parse.urlencode({"thread_id": thread_id})
    url = f"{_worker_base_url()}/api/v1/git/admin/dispatch-status?{query}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_PROBE_TIMEOUT_S) as resp:
            raw = resp.read().decode("utf-8")
            http_status = resp.status
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return ProbeResult(payload=None, http_status=404, error=None)
        return ProbeResult(
            payload=None,
            http_status=exc.code,
            error=f"http_error_{exc.code}",
        )
    except (TimeoutError, urllib.error.URLError, OSError) as exc:
        return ProbeResult(
            payload=None, http_status=None, error=f"probe_unreachable:{exc}"
        )

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return ProbeResult(
            payload=None, http_status=http_status, error="malformed_json"
        )

    if not isinstance(payload, dict):
        return ProbeResult(
            payload=None, http_status=http_status, error="malformed_json"
        )
    return ProbeResult(payload=payload, http_status=http_status, error=None)


def evaluate_link_liveness(
    *,
    thread_id: str,
    link_execution_id: str | None,
    sole_link: bool = True,
    probe_fn=probe_dispatch_status,
    probe_capture: dict[str, Any] | None = None,
) -> tuple[LivenessVerdict, str, str | None]:
    """Probe GIW and classify whether orphan-reconcile or admitted-TTL reap may proceed.

    When ``probe_capture`` is set, it receives the probe ``execution_id`` so a
    terminal backfill can fan out only when that id is the link's id.
    """
    probe = probe_fn(thread_id)
    if probe_capture is not None:
        payload = probe.payload if isinstance(probe.payload, dict) else None
        probe_capture["execution_id"] = (
            None if payload is None else payload.get("execution_id")
        )
    verdict, reason, terminal_status = classify_probe(
        probe,
        link_execution_id=link_execution_id,
        sole_link=sole_link,
    )
    if verdict is LivenessVerdict.DEFER:
        logger.warning(
            "sdk liveness probe deferred orphan for thread=%s execution=%s: %s",
            thread_id,
            link_execution_id,
            reason,
        )
    return verdict, reason, terminal_status


def _linked_at_within_grace(linked_at: object, now: datetime) -> bool:
    """True when ``linked_at`` parses and its age is inside the admit grace."""
    if linked_at is None or linked_at == "":
        return False
    try:
        parsed = parse_ts(str(linked_at))
    except (ValueError, TypeError):
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    clock = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    return clock - parsed <= producer_liveness_grace()


def reader_liveness_witness(
    *,
    thread_id: str,
    execution_id: str,
    linked_at: object,
    now: datetime,
) -> Literal["live", "dead"] | None:
    """Witness whether a null-terminal link is live, without writing ``terminal_status``.

    Inside the admit grace this returns None and does not call GIW, so a dead
    probe cannot override ``admit_grace``. Past the grace, ``SKIP_LIVE`` is
    ``live`` and ``ALLOW_ORPHAN`` / ``heartbeat_stale`` is ``dead``. Probe
    errors, timeouts, and every other verdict return None. Does not call
    ``terminate_dispatch``.
    """
    if _linked_at_within_grace(linked_at, now):
        return None
    verdict, reason, _terminal = evaluate_link_liveness(
        thread_id=thread_id,
        link_execution_id=execution_id,
    )
    if verdict is LivenessVerdict.SKIP_LIVE:
        return "live"
    if verdict is LivenessVerdict.ALLOW_ORPHAN and reason == "heartbeat_stale":
        return "dead"
    return None

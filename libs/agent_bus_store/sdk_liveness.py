"""SDK dispatch liveness probe for orphan reconcile gating."""

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
from typing import Any

logger = logging.getLogger("agent-bus.sdk_liveness")

_LIVE_STATUSES = frozenset({"queued", "admitted", "running"})
_TERMINAL_STATUSES = frozenset({"completed", "failed"})
_HEARTBEAT_STALE_S: float = float(
    os.getenv("AGENT_BUS_SDK_HEARTBEAT_STALE_S", "300")
)
_PROBE_TIMEOUT_S: float = float(os.getenv("AGENT_BUS_SDK_PROBE_TIMEOUT_S", "2"))


class LivenessVerdict(StrEnum):
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


def classify_probe(
    probe: ProbeResult,
    *,
    link_execution_id: str | None,
) -> tuple[LivenessVerdict, str, str | None]:
    """Classify a probe outcome. Returns (verdict, reason, terminal_status)."""
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

    probe_execution_id = payload.get("execution_id")
    if (
        link_execution_id
        and probe_execution_id
        and str(probe_execution_id) != str(link_execution_id)
    ):
        return LivenessVerdict.ALLOW_ORPHAN, "execution_id_mismatch", None

    if status in _TERMINAL_STATUSES:
        return LivenessVerdict.TERMINAL_BACKFILL, "probe_terminal", status

    if status not in _LIVE_STATUSES:
        return LivenessVerdict.DEFER, f"probe_status_unknown_{status}", None

    freshness = heartbeat_freshness(payload.get("last_heartbeat_at"))
    if freshness == "indeterminate":
        return LivenessVerdict.DEFER, "heartbeat_indeterminate", None
    if freshness == "stale":
        return LivenessVerdict.ALLOW_ORPHAN, "heartbeat_stale", None
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
        return ProbeResult(payload=None, http_status=None, error=f"probe_unreachable:{exc}")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return ProbeResult(payload=None, http_status=http_status, error="malformed_json")

    if not isinstance(payload, dict):
        return ProbeResult(payload=None, http_status=http_status, error="malformed_json")
    return ProbeResult(payload=payload, http_status=http_status, error=None)


def evaluate_link_liveness(
    *,
    thread_id: str,
    link_execution_id: str | None,
    probe_fn=probe_dispatch_status,
) -> tuple[LivenessVerdict, str, str | None]:
    """Probe worker and classify whether orphan reconcile may proceed."""
    probe = probe_fn(thread_id)
    verdict, reason, terminal_status = classify_probe(
        probe, link_execution_id=link_execution_id
    )
    if verdict is LivenessVerdict.DEFER:
        logger.warning(
            "sdk liveness probe deferred orphan for thread=%s execution=%s: %s",
            thread_id,
            link_execution_id,
            reason,
        )
    return verdict, reason, terminal_status

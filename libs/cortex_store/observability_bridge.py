"""Observability bridge — entity-keyed access to Event Service and Agent Bus.

Queries the Event Service (realtime-snapshot, stack-last-started, signal-events)
and Agent Bus (active threads) over UDS to build the in_flight section and
thread matching for entity_status.
"""

from __future__ import annotations

import datetime
import json
import os
from typing import Any

from transport_utils import make_sync_client
from universal_logging import get_logger

from .status_models import (
    InFlightRequest,
    InFlightSection,
    RecentCompletion,
    RecentFailure,
    ThreadReference,
)

logger = get_logger("cortex-api.observability_bridge")

_EVENTS_QUERY_URL = f"unix://{os.environ.get('EVENTS_QUERY_SOCK', '/tmp/universal-protocol/events-query.sock')}"
_AGENT_BUS_URL = f"unix://{os.environ.get('AGENT_BUS_SOCK', '/tmp/universal-protocol/agent-bus.sock')}"
_AGENT_BUS_TOKEN = os.environ.get("AGENT_BUS_TOKEN", "")


def _signal_matches(signal: str, pattern: str) -> bool:
    if "*" not in pattern:
        return signal == pattern
    return signal.startswith(pattern.rstrip("*"))


def _parse_ts(ts_str: str | None) -> datetime.datetime | None:
    if not ts_str:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.datetime.strptime(ts_str, fmt).replace(tzinfo=datetime.UTC)
        except ValueError:
            continue
    return None


def _parse_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


def query_event_service(operation: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        with make_sync_client(_EVENTS_QUERY_URL, timeout=5.0) as client:
            resp = client.post(
                "/v1/query",
                json={"type": "operation", "name": operation, "params": params},
            )
            if resp.status_code == 200:
                return resp.json()
            logger.warning("Event Service %s returned %d", operation, resp.status_code)
    except Exception:
        logger.warning("Event Service unreachable for %s", operation, exc_info=True)
    return {}


def query_agent_bus_threads() -> list[dict[str, Any]]:
    if not _AGENT_BUS_TOKEN:
        return []
    try:
        with make_sync_client(_AGENT_BUS_URL, timeout=5.0) as client:
            resp = client.get(
                "/threads",
                params={"status": "active"},
                headers={"Authorization": f"Bearer {_AGENT_BUS_TOKEN}"},
            )
            if resp.status_code == 200:
                return resp.json().get("threads", [])
    except Exception:
        logger.warning("Agent-bus unreachable for thread query", exc_info=True)
    return []


def match_threads(
    threads: list[dict[str, Any]],
    entity_name: str,
    aliases: list[str] | None,
) -> list[ThreadReference]:
    keywords = [entity_name.lower()]
    if aliases:
        keywords.extend(a.lower() for a in aliases)
    matches: list[ThreadReference] = []
    for t in threads:
        slug = (t.get("slug") or "").lower()
        last_subject = (t.get("last_subject") or "").lower()
        if any(kw in slug or kw in last_subject for kw in keywords):
            matches.append(
                ThreadReference(
                    thread=t["id"],
                    slug=t.get("slug", ""),
                    unread=t.get("unread_count", 0),
                )
            )
    return matches


def build_in_flight(
    attributes: dict[str, Any] | None,
) -> InFlightSection | None:
    """Observability bridge: entity-keyed access to live operational state."""
    if not attributes:
        return None
    signal_patterns: list[str] | None = attributes.get("observability_signals")
    if not signal_patterns:
        return None

    service_name = attributes.get("service_name", "")
    startup_signal = attributes.get("startup_signal", "")

    started_data = query_event_service("stack-last-started", {})
    service_started: str | None = None
    for row in started_data.get("rows", []):
        sig = row.get("signal", "")
        if startup_signal and sig == startup_signal:
            service_started = row.get("timestamp")
            break
        if not startup_signal and service_name and service_name in sig:
            service_started = row.get("timestamp")
            break

    snapshot = query_event_service("realtime-snapshot", {"limit": 50})
    now = datetime.datetime.now(tz=datetime.UTC)
    active_requests: list[InFlightRequest] = []
    for row in snapshot.get("rows", []):
        sig = row.get("signal", "")
        if not any(_signal_matches(sig, p) for p in signal_patterns):
            continue
        payload = _parse_payload(row.get("payload"))
        started_at = row.get("timestamp")
        elapsed = 0.0
        ts = _parse_ts(started_at)
        if ts:
            elapsed = (now - ts).total_seconds()
        active_requests.append(
            InFlightRequest(
                request_id=payload.get("request_id") or row.get("execution_id"),
                operation=sig,
                phase=payload.get("phase"),
                started_at=started_at,
                elapsed_seconds=round(elapsed, 1),
                last_blocking_reason=payload.get("blocking_reason"),
            )
        )

    recent_completions: list[RecentCompletion] = []
    recent_failures: list[RecentFailure] = []
    for pattern in signal_patterns:
        events = query_event_service("signal-events", {"signal": pattern, "limit": 10})
        for row in events.get("rows", []):
            sig = row.get("signal", "")
            payload = _parse_payload(row.get("payload"))
            if sig.endswith(".failed") or sig.endswith(".error"):
                recent_failures.append(
                    RecentFailure(
                        request_id=payload.get("request_id") or row.get("execution_id"),
                        operation=sig,
                        failed_at=row.get("timestamp"),
                        error=payload.get("error") or payload.get("message"),
                    )
                )
            elif sig.endswith(".completed") or sig.endswith(".done"):
                recent_completions.append(
                    RecentCompletion(
                        request_id=payload.get("request_id") or row.get("execution_id"),
                        operation=sig,
                        completed_at=row.get("timestamp"),
                        duration_ms=payload.get("duration_ms"),
                        status="success",
                    )
                )

    return InFlightSection(
        service_last_started=service_started,
        active_requests=active_requests,
        recent_completions=recent_completions[:5],
        recent_failures=recent_failures[:5],
    )

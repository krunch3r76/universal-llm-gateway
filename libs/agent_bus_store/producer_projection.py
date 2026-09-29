"""Producer-link projection for the agent-bus wait endpoint.

``state=in_flight`` is a positive liveness signal. ``terminal_status IS NULL``
alone is not one: a stream that dies without ``terminate_dispatch`` used to
read ``in_flight`` forever (friction a:36832). This module does not write
``terminal_status`` — a live producer must not be terminalized from a reader
(a:36651).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

ProducerState = Literal["unknown", "unlinked", "in_flight", "terminal"]
LivenessWitness = Literal["live", "dead"]

_SOURCE = "thread_dispatch_links"
_RECENT_TERMINAL_WINDOW = timedelta(hours=24)
# Same default as the SDK heartbeat stale bound. A link with no progress beat
# older than this is not evidence the producer is alive.
_DEFAULT_LIVENESS_GRACE_S = 300.0


def producer_liveness_grace() -> timedelta:
    """Admit-grace window for a non-terminal link with no other witness.

    Env ``AGENT_BUS_PRODUCER_LIVENESS_GRACE_S`` (seconds). Invalid or negative
    values fall back to 300.
    """
    raw = os.getenv("AGENT_BUS_PRODUCER_LIVENESS_GRACE_S", "")
    try:
        seconds = float(raw) if raw else _DEFAULT_LIVENESS_GRACE_S
    except ValueError:
        seconds = _DEFAULT_LIVENESS_GRACE_S
    if seconds < 0:
        seconds = _DEFAULT_LIVENESS_GRACE_S
    return timedelta(seconds=seconds)


def nonterminal_link_state(
    *,
    linked_at: Any,
    now: datetime,
    liveness_witness: LivenessWitness | None = None,
) -> tuple[ProducerState, str]:
    """Classify a row whose ``terminal_status`` is NULL. Does not write it.

    Live: ``liveness_witness='live'`` (``witness_live``) or ``linked_at`` inside
    the admit grace (``admit_grace``). Dead with no terminal write:
    ``liveness_witness='dead'`` reports ``unknown`` /
    ``stream_dead_no_terminal``. Cannot tell: missing or stale ``linked_at``
    reports ``unknown`` / ``no_liveness_signal``.
    """
    if liveness_witness == "live":
        return "in_flight", "witness_live"
    if liveness_witness == "dead":
        return "unknown", "stream_dead_no_terminal"
    parsed = _parse_link_timestamp(linked_at)
    if parsed is None:
        return "unknown", "no_liveness_signal"
    clock = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    if clock - parsed <= producer_liveness_grace():
        return "in_flight", "admit_grace"
    return "unknown", "no_liveness_signal"


def classify_producer_link(
    *,
    execution_id: str | None,
    dispatch_links: list[dict[str, Any]],
    now: datetime | None = None,
    liveness_witness: LivenessWitness | None = None,
) -> dict[str, Any]:
    """Classify dispatch-link liveness for one pinned execution.

    Always returns the advisory ``producer`` container with keys:
    ``execution_id``, ``pipeline_id``, ``state``, ``terminal_status``,
    ``linked_at``, ``delivery_at``, ``source``, ``liveness_reason``.

    Does not mutate ``dispatch_links`` and does not write ``terminal_status``.
    """
    if not execution_id:
        return {
            "execution_id": None,
            "pipeline_id": None,
            "state": "unknown",
            "terminal_status": None,
            "linked_at": None,
            "delivery_at": None,
            "source": _SOURCE,
            "liveness_reason": "execution_omitted",
        }

    row = next(
        (link for link in dispatch_links if link.get("execution_id") == execution_id),
        None,
    )
    if row is None:
        return {
            "execution_id": execution_id,
            "pipeline_id": None,
            "state": "unlinked",
            "terminal_status": None,
            "linked_at": None,
            "delivery_at": None,
            "source": _SOURCE,
            "liveness_reason": "no_row",
        }

    terminal_status = row.get("terminal_status")
    if terminal_status:
        state: ProducerState = "terminal"
        liveness_reason = "terminal_status"
    else:
        state, liveness_reason = nonterminal_link_state(
            linked_at=row.get("linked_at"),
            now=now or datetime.now(UTC),
            liveness_witness=liveness_witness,
        )
    out: dict[str, Any] = {
        "execution_id": execution_id,
        "pipeline_id": row.get("pipeline_id"),
        "state": state,
        "terminal_status": terminal_status,
        "linked_at": row.get("linked_at"),
        "delivery_at": row.get("delivery_at"),
        "source": _SOURCE,
        "liveness_reason": liveness_reason,
    }
    archive_uri = row.get("archive_uri")
    if archive_uri:
        out["archive_uri"] = archive_uri
    return out


def _parse_link_timestamp(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts


def _terminal_link_within_recent_window(link: dict[str, Any], *, now: datetime) -> bool:
    cutoff = now - _RECENT_TERMINAL_WINDOW
    for key in ("delivery_at", "linked_at"):
        ts = _parse_link_timestamp(link.get(key))
        if ts is not None and ts >= cutoff:
            return True
    return False


def project_thread_producers(
    dispatch_links: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Classify dispatch links for the additive ``producers`` wait field.

    Non-terminal links are listed first in ``linked_at`` order (their
    ``state`` is ``in_flight`` only inside the admit grace; older rows are
    ``unknown``). Then terminal links whose ``delivery_at`` or ``linked_at``
    falls within the last 24 hours.
    """
    if not dispatch_links:
        return []
    clock = now or datetime.now(UTC)
    nonterminal: list[dict[str, Any]] = []
    terminal_recent: list[dict[str, Any]] = []
    for link in dispatch_links:
        if link.get("terminal_status") is None:
            nonterminal.append(link)
        elif _terminal_link_within_recent_window(link, now=clock):
            terminal_recent.append(link)
    ordered = nonterminal + terminal_recent
    return [
        classify_producer_link(
            execution_id=str(link.get("execution_id") or ""),
            dispatch_links=dispatch_links,
            now=clock,
        )
        for link in ordered
        if link.get("execution_id")
    ]

"""In-flight producer rows for CHECKPOINT derived zones (O14-D3)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .db.lineage import LineageDispatchLink

_TERMINAL_RETENTION = timedelta(hours=24)
_STALE_IN_FLIGHT_RETENTION = timedelta(hours=24)
# O14-D3 on-bus surface is a sample; full registry lives on wait/lineage.
CHECKPOINT_MAX_PRODUCER_ROWS = 8
_PRODUCER_OVERFLOW_LINE = (
    "_+{extra} more · full registry: agent_bus wait `producers[]` or thread lineage_"
)


@dataclass(frozen=True, slots=True)
class ProducerDispatchRow:
    """One dispatch-link row rendered under ``### In-flight producers``."""

    lane_thread_id: str
    execution_id: str
    model_or_seat: str
    state: str
    linked_at: str


def transcript_projection_registry_uri(root_thread: str) -> str:
    """Canonical registry sidecar for checkpoint derived-zone pointers."""
    return (
        f"cortex://notes/system/threads/{root_thread}-transcript-projection.md"
    )


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _model_or_seat(link: LineageDispatchLink) -> str:
    if link.caller_agent:
        return link.caller_agent
    return link.pipeline_id


def _producer_state(link: LineageDispatchLink) -> str:
    return "terminal" if link.terminal_status else "in_flight"


def _terminal_visible(link: LineageDispatchLink, *, now: datetime) -> bool:
    ref = _parse_ts(link.delivery_at) or _parse_ts(link.terminal_at) or _parse_ts(
        link.linked_at
    )
    if ref is None:
        return False
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=UTC)
    return now - ref <= _TERMINAL_RETENTION


def _in_flight_fresh(link: LineageDispatchLink, *, now: datetime) -> bool:
    """In-flight links without activity for 24h are stale on the CP surface."""
    linked = _parse_ts(link.linked_at)
    if linked is None:
        return False
    if linked.tzinfo is None:
        linked = linked.replace(tzinfo=UTC)
    return now - linked <= _STALE_IN_FLIGHT_RETENTION


def filter_visible_producer_links(
    links: tuple[LineageDispatchLink, ...],
    *,
    lane_thread_id: str,
    now: datetime | None = None,
) -> tuple[ProducerDispatchRow, ...]:
    """Keep in-flight links and terminal links not older than 24h."""
    instant = now or datetime.now(UTC)
    rows: list[ProducerDispatchRow] = []
    for link in links:
        if link.terminal_status is None:
            visible = True
        else:
            visible = _terminal_visible(link, now=instant)
        if not visible:
            continue
        rows.append(
            ProducerDispatchRow(
                lane_thread_id=lane_thread_id,
                execution_id=link.execution_id,
                model_or_seat=_model_or_seat(link),
                state=_producer_state(link),
                linked_at=link.linked_at,
            )
        )
    return tuple(rows)


def filter_cp_projection_producer_links(
    links: tuple[LineageDispatchLink, ...],
    *,
    lane_thread_id: str,
    now: datetime | None = None,
) -> tuple[ProducerDispatchRow, ...]:
    """CP projection subset — excludes stale in-flight links (tier 3).

    Wait ``producers[]`` and lineage keep the full ``filter_visible_producer_links``
    set; only the CHECKPOINT derived zone uses this stricter filter.
    """
    instant = now or datetime.now(UTC)
    rows: list[ProducerDispatchRow] = []
    for link in links:
        if link.terminal_status is None:
            if not _in_flight_fresh(link, now=instant):
                continue
        elif not _terminal_visible(link, now=instant):
            continue
        rows.append(
            ProducerDispatchRow(
                lane_thread_id=lane_thread_id,
                execution_id=link.execution_id,
                model_or_seat=_model_or_seat(link),
                state=_producer_state(link),
                linked_at=link.linked_at,
            )
        )
    return tuple(rows)


def render_producer_row(row: ProducerDispatchRow) -> str:
    exec_short = row.execution_id[:8]
    return (
        f"- agent-bus:{row.lane_thread_id} · {exec_short} · "
        f"{row.model_or_seat} · {row.state} since {row.linked_at}"
    )


def _sort_producer_rows_for_display(
    rows: tuple[ProducerDispatchRow, ...],
) -> tuple[ProducerDispatchRow, ...]:
    """In-flight first, newest ``linked_at`` first — matches wait ``producers[]``."""

    def _key(row: ProducerDispatchRow) -> tuple[int, float]:
        state_rank = 0 if row.state == "in_flight" else 1
        ts = _parse_ts(row.linked_at)
        return (state_rank, -(ts.timestamp() if ts else 0.0))

    return tuple(sorted(rows, key=_key))


def render_producers_section(
    rows: tuple[ProducerDispatchRow, ...],
    *,
    root_thread: str,
    summary_mode: bool = True,
) -> list[str]:
    """Return markdown lines for the In-flight producers subsection."""
    parts = ["### In-flight producers"]
    if not rows:
        parts.append("_none linked_")
        return parts
    if summary_mode:
        in_flight = sum(1 for row in rows if row.state == "in_flight")
        registry = transcript_projection_registry_uri(root_thread)
        parts.append(
            f"producers: {in_flight} in_flight · registry: {registry}"
        )
        return parts
    ordered = _sort_producer_rows_for_display(rows)
    visible = ordered[:CHECKPOINT_MAX_PRODUCER_ROWS]
    parts.extend(render_producer_row(row) for row in visible)
    extra = len(ordered) - len(visible)
    if extra:
        parts.append(_PRODUCER_OVERFLOW_LINE.format(extra=extra))
    return parts


__all__ = [
    "CHECKPOINT_MAX_PRODUCER_ROWS",
    "ProducerDispatchRow",
    "filter_cp_projection_producer_links",
    "filter_visible_producer_links",
    "render_producer_row",
    "render_producers_section",
    "transcript_projection_registry_uri",
]

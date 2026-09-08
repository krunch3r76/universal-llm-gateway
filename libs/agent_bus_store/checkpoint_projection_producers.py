"""In-flight producer rows for CHECKPOINT derived zones (O14-D3)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .db.lineage import LineageDispatchLink

_TERMINAL_RETENTION = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class ProducerDispatchRow:
    """One dispatch-link row rendered under ``### In-flight producers``."""

    lane_thread_id: str
    execution_id: str
    model_or_seat: str
    state: str
    linked_at: str


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


def render_producer_row(row: ProducerDispatchRow) -> str:
    exec_short = row.execution_id[:8]
    return (
        f"- agent-bus:{row.lane_thread_id} · {exec_short} · "
        f"{row.model_or_seat} · {row.state} since {row.linked_at}"
    )


def render_producers_section(rows: tuple[ProducerDispatchRow, ...]) -> list[str]:
    """Return markdown lines for the In-flight producers subsection."""
    parts = ["### In-flight producers"]
    if rows:
        parts.extend(render_producer_row(row) for row in rows)
    else:
        parts.append("_none linked_")
    return parts


__all__ = [
    "ProducerDispatchRow",
    "filter_visible_producer_links",
    "render_producer_row",
    "render_producers_section",
]

"""Single catch-up pass for the ULG story projector."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from universal_logging import get_logger

from .checkpoint import (
    ProjectorCheckpoint,
    load_checkpoint,
    max_seq_in_journal_text,
    save_checkpoint,
)
from .event_query import (
    events_query_available,
    parse_payload,
    query_event_timestamp,
    query_events_since_seq,
    query_oldest_live_seq,
)
from .journal import append_line, shard_key_from_ts_ms, shard_path
from .render import render_event_line, render_gap_line, render_parse_failure

logger = get_logger(__name__)


def _fmt_wall(ts_ms: int | None) -> str:
    if ts_ms is None:
        return "unknown time"
    return datetime.fromtimestamp(ts_ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")


def _recover_checkpoint(checkpoint: ProjectorCheckpoint) -> ProjectorCheckpoint:
    """Align checkpoint with journal tail so append-before-checkpoint never duplicates."""
    highest = 0
    root = shard_path("2099-01").parent
    if root.is_dir():
        for path in root.glob("*.md"):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            highest = max(highest, max_seq_in_journal_text(text))
    if highest > checkpoint.last_seq:
        checkpoint.last_seq = highest
        checkpoint.updated_at = datetime.now(UTC).isoformat()
        save_checkpoint(checkpoint)
    return checkpoint


def _write_gap_if_needed(
    checkpoint: ProjectorCheckpoint,
    *,
    oldest_live: int,
    now: datetime,
) -> ProjectorCheckpoint:
    if checkpoint.last_seq >= oldest_live:
        return checkpoint
    lost_from = checkpoint.last_seq + 1
    lost_through = oldest_live - 1
    if lost_through < lost_from:
        checkpoint.last_seq = oldest_live - 1
        return checkpoint

    wall_from = _fmt_wall(query_event_timestamp(checkpoint.last_seq))
    wall_through = _fmt_wall(query_event_timestamp(oldest_live))
    gap_seq = oldest_live
    line = render_gap_line(
        seq=gap_seq,
        lost_from_seq=lost_from,
        lost_through_seq=lost_through,
        wall_from=wall_from,
        wall_through=wall_through,
    )
    append_line(
        line,
        shard_key=now.strftime("%Y-%m"),
        ensure_epoch=not checkpoint.epoch_written,
        epoch_started_at=now,
    )
    checkpoint.epoch_written = True
    checkpoint.last_seq = oldest_live - 1
    checkpoint.updated_at = now.isoformat()
    save_checkpoint(checkpoint)
    logger.warning(
        "ulg-story retention gap admitted: seq %s–%s (%s to %s)",
        lost_from,
        lost_through,
        wall_from,
        wall_through,
    )
    return checkpoint


def _process_event(
    row: dict[str, Any],
    checkpoint: ProjectorCheckpoint,
    *,
    now: datetime,
) -> ProjectorCheckpoint:
    seq = int(row["seq"])
    signal = str(row.get("signal") or "")
    ts_ms = row.get("ts_unix_ms")
    ts_ms_int = int(ts_ms) if ts_ms is not None else None
    shard_key = shard_key_from_ts_ms(ts_ms_int)

    try:
        payload = parse_payload(row.get("payload"))
        line = render_event_line(seq=seq, signal=signal, payload=payload)
    except Exception as exc:  # noqa: BLE001 — failure-isolated per event
        line = render_parse_failure(seq=seq, signal=signal, reason=str(exc))

    if line is None:
        line = render_parse_failure(
            seq=seq,
            signal=signal,
            reason="signal not mapped",
        )

    append_line(
        line,
        shard_key=shard_key,
        ensure_epoch=not checkpoint.epoch_written,
        epoch_started_at=now,
    )
    checkpoint.epoch_written = True
    checkpoint.last_seq = seq
    checkpoint.updated_at = now.isoformat()
    save_checkpoint(checkpoint)
    return checkpoint


def run_projector_once() -> dict[str, Any]:
    """Execute one catch-up pass. Never raises — failure-isolated from GIW hot paths."""
    if not events_query_available():
        return {"ok": False, "reason": "events_query_unavailable", "processed": 0}

    now = datetime.now(UTC)
    checkpoint = _recover_checkpoint(load_checkpoint())
    oldest_live = query_oldest_live_seq()
    if oldest_live is None:
        return {"ok": True, "reason": "store_empty", "processed": 0}

    checkpoint = _write_gap_if_needed(checkpoint, oldest_live=oldest_live, now=now)

    processed = 0
    while True:
        rows = query_events_since_seq(checkpoint.last_seq, limit=200)
        if not rows:
            break
        for row in rows:
            checkpoint = _process_event(row, checkpoint, now=now)
            processed += 1
        if len(rows) < 200:
            break

    return {
        "ok": True,
        "processed": processed,
        "last_seq": checkpoint.last_seq,
        "epoch_written": checkpoint.epoch_written,
    }

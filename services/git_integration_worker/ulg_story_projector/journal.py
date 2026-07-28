"""Monthly Cortex journal shard writer for ULG story wire."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from implement_admission.closeout_helpers import cortex_files_root

_JOURNAL_REL = Path("notes/system/journal/ulg-story")


def journal_root() -> Path:
    return cortex_files_root() / _JOURNAL_REL


def shard_key_from_ts_ms(ts_ms: int | None) -> str:
    if ts_ms is None or ts_ms <= 0:
        return datetime.now(UTC).strftime("%Y-%m")
    return datetime.fromtimestamp(ts_ms / 1000, tz=UTC).strftime("%Y-%m")


def shard_path(shard_key: str) -> Path:
    return journal_root() / f"{shard_key}.md"


def epoch_line(*, started_at: datetime | None = None) -> str:
    when = (started_at or datetime.now(UTC)).strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"Epoch: this journal begins at projector start ({when}); "
        "earlier ULG history is unrecoverable from the seven-day event window. "
        "No backfill attempted.\n"
    )


def append_line(
    line: str,
    *,
    shard_key: str,
    ensure_epoch: bool = False,
    epoch_started_at: datetime | None = None,
) -> None:
    path = shard_path(shard_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = ""
    if ensure_epoch and not path.exists():
        prefix = epoch_line(started_at=epoch_started_at)
    with path.open("a", encoding="utf-8") as handle:
        if prefix:
            handle.write(prefix)
        handle.write(line.rstrip() + "\n")

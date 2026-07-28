"""Durable checkpoint for ULG story projector catch-up."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_SEQ_TAIL_RE = re.compile(r"\[seq:(\d+)")


@dataclass(slots=True)
class ProjectorCheckpoint:
    last_seq: int
    epoch_written: bool
    updated_at: str


def checkpoint_path() -> Path:
    data_dir = Path(os.getenv("DATA_DIR", str(Path.home() / ".gateway"))).expanduser()
    return data_dir / "ulg-story-projector-checkpoint.json"


def load_checkpoint() -> ProjectorCheckpoint:
    path = checkpoint_path()
    if not path.is_file():
        return ProjectorCheckpoint(
            last_seq=0,
            epoch_written=False,
            updated_at="",
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ProjectorCheckpoint(last_seq=0, epoch_written=False, updated_at="")
    return ProjectorCheckpoint(
        last_seq=int(raw.get("last_seq") or 0),
        epoch_written=bool(raw.get("epoch_written")),
        updated_at=str(raw.get("updated_at") or ""),
    )


def save_checkpoint(checkpoint: ProjectorCheckpoint) -> None:
    path = checkpoint_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_seq": checkpoint.last_seq,
        "epoch_written": checkpoint.epoch_written,
        "updated_at": checkpoint.updated_at
        or datetime.now(UTC).isoformat(),
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def max_seq_in_journal_text(text: str) -> int:
    highest = 0
    for match in _SEQ_TAIL_RE.finditer(text):
        highest = max(highest, int(match.group(1)))
    return highest

"""Per-hop land registry — fired / landed / expired independent of JSONL lag.

Transcript grep alone cannot boot retire: Cursor often births the agent-transcript
JSONL minutes after submit (a:38474 measured gaps; specimen 15441 tip_cp=75 at
+842s). The resume-fence journal arms at ``beforeSubmitPrompt`` with the new
conversation id — that is land proof. This file records the hop's own lifecycle
so the departing tab can quiesce at ``fired`` and retire only at ``landed``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Literal

from durable_io.atomic import durable_write_text

from bus_watch.fable_lock import WATCH_DIR

HopLandState = Literal["fired", "landed", "expired"]
DEFAULT_LANDING_TIMEOUT_S = 600.0


def hop_land_path(root_id: str, watch_dir: Path = WATCH_DIR) -> Path:
    return watch_dir / f"liaison-{root_id}.hop-land.json"


def write_hop_land(record: dict[str, Any], *, watch_dir: Path = WATCH_DIR) -> Path:
    """Durable-write the hop-land record for ``record['root']``."""
    root = str(record.get("root") or "").strip()
    if not root:
        raise ValueError("hop land record requires root")
    path = hop_land_path(root, watch_dir)
    watch_dir.mkdir(parents=True, exist_ok=True)
    durable_write_text(path, json.dumps(record, indent=2, sort_keys=True) + "\n")
    return path


def read_hop_land(root_id: str, *, watch_dir: Path = WATCH_DIR) -> dict[str, Any] | None:
    path = hop_land_path(root_id, watch_dir)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def mark_fired(
    *,
    root: str,
    tip_cp: int | None,
    seal_bus_turn: int | None,
    departing_transcript_id: str | None,
    fired_at: float | None = None,
    deadline_s: float = DEFAULT_LANDING_TIMEOUT_S,
    message_path: str | None = None,
    watch_dir: Path = WATCH_DIR,
) -> dict[str, Any]:
    """Write ``state=fired`` after keystroke; departing tab must quiesce."""
    t = time.time() if fired_at is None else fired_at
    record: dict[str, Any] = {
        "root": str(root),
        "tip_cp": tip_cp,
        "seal_bus_turn": seal_bus_turn,
        "fired_at": t,
        "deadline_at": t + float(deadline_s),
        "departing_transcript_id": (departing_transcript_id or "").strip() or None,
        "state": "fired",
        "landed_transcript_id": None,
        "proof": None,
        "message_path": message_path,
    }
    write_hop_land(record, watch_dir=watch_dir)
    return record


def mark_landed(
    record: dict[str, Any],
    *,
    landed_transcript_id: str,
    proof: Literal["fence", "transcript", "pre_existing"],
    watch_dir: Path = WATCH_DIR,
) -> dict[str, Any]:
    """Advance a fired record to ``landed``."""
    updated = {
        **record,
        "state": "landed",
        "landed_transcript_id": landed_transcript_id,
        "proof": proof,
        "landed_at": time.time(),
    }
    write_hop_land(updated, watch_dir=watch_dir)
    return updated


def mark_expired(
    record: dict[str, Any], *, watch_dir: Path = WATCH_DIR
) -> dict[str, Any]:
    """Advance a fired record to ``expired`` — OPERATOR_GATE, never re-fire."""
    updated = {**record, "state": "expired", "expired_at": time.time()}
    write_hop_land(updated, watch_dir=watch_dir)
    return updated


__all__ = [
    "DEFAULT_LANDING_TIMEOUT_S",
    "HopLandState",
    "hop_land_path",
    "mark_expired",
    "mark_fired",
    "mark_landed",
    "read_hop_land",
    "write_hop_land",
]

"""Durable post-hoc records for harvest-wanted propagation consumption."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .db import charter_runner_data_dir


def propagation_outcomes_path() -> Path:
    """JSONL sink for unattended harvest-wanted fire outcomes."""
    return charter_runner_data_dir() / "propagation-outcomes.jsonl"


def append_propagation_outcome(record: dict[str, Any]) -> Path:
    """Append one consumption outcome line — readable by a later seat."""
    path = propagation_outcomes_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = {**record, "recorded_at": time.time()}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, sort_keys=True) + "\n")
    return path


__all__ = ["append_propagation_outcome", "propagation_outcomes_path"]

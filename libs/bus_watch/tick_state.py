"""Liaison tick state file — load, save, and absorb sibling-process edits.

The tick state (``tmp/watchers/liaison-<root>.tick.json``) has two writers: the
long-running ``--loop`` / ``--spawn-on-wake`` process, which keeps its counters in
memory and persists on every due tick, and short-lived operator commands
(``--set KEY=VALUE``, ``--mark-checkpoint``, ``--mark-relayed``) run from another
shell. Without a merge the loop's next ``save_state`` silently reverts the
operator's edit (observed 2026-09-12 03:04Z: ``ready=true`` and the hop cap were
lost within one poll). ``absorb_operator_edits`` re-reads the operator-owned keys
before each tick so the loop's write carries them forward.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Keys only the operator's one-shot commands write; the loop never derives them.
OPERATOR_KEYS: tuple[str, ...] = ("policy", "relayed_watchers", "last_cp_tick")


def load_state(path: Path) -> dict[str, Any]:
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return {}
    return {}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def absorb_operator_edits(state: dict[str, Any], path: Path) -> list[str]:
    """Copy operator-owned keys from disk into the loop's in-memory ``state``.

    Returns the keys whose value changed so the loop can log the steer.
    """
    disk = load_state(path)
    changed: list[str] = []
    for key in OPERATOR_KEYS:
        if key in disk and disk[key] != state.get(key):
            state[key] = disk[key]
            changed.append(key)
    return changed


__all__ = ["OPERATOR_KEYS", "absorb_operator_edits", "load_state", "save_state"]

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
from collections.abc import Callable
from pathlib import Path
from typing import Any

# Keys only the operator's one-shot commands write; the loop never derives them.
# ``register`` joined 2026-09-12 07:10Z: the gear-3 ticker clobbered an attended→autonomous
# flip (--register) on its next save because the flip was not an absorbed key.
# ``handoff`` (``--go-under``) must reach a ticker already polling, or the verb
# arms nothing until the ticker restarts. ``friction_dispositions``
# (``--mark-friction``) is the seat's bind on a friction score row; the ticker
# only reads it (``bus_watch.friction_rows``).
# The ``last_induction_*`` / ``navigator_commissions_*`` group joined
# 2026-09-14 13:30Z. ``liaison-induce.py --fire`` is a one-shot the loop never
# derives from, so the loop's next save dropped the navigator's own throttle
# state within one poll (~160s). That silently disarms
# ``navigator_grace_elapsed``: with ``last_induction_at`` gone the grace always
# reads elapsed, leaving single-flight as the only brake, so a periodic sweep
# re-fires a cdp wake the moment the previous one completes instead of honouring
# ``navigator_grace_seconds`` (900). Caught when the navigator clock was
# installed and the state file still read ``last_induction_at: None`` directly
# after a fire that had returned a live execution_id.
OPERATOR_KEYS: tuple[str, ...] = (
    "policy",
    "relayed_watchers",
    "last_cp_tick",
    "register",
    "handoff",
    "friction_dispositions",
    "last_induction_at",
    "last_induction_fingerprint",
    "navigator_commissions_by_night",
    "navigator_commissions_tonight",
)


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


def update_state(
    path: Path, mutate: Callable[[dict[str, Any]], None]
) -> dict[str, Any]:
    """Read-modify-write against the file as it is *now*; returns the new state.

    Every one-shot writer must go through here instead of saving the snapshot
    it loaded earlier. ``liaison-induce.py --fire`` blocks on the GUI hop for
    minutes and then saved its snapshot: 10479 2026-09-13 06:52Z re-planted
    ``policy.ready=false`` that ``--go-under`` had dropped 18 minutes before,
    and the ticker absorbed it as an operator steer.
    """
    current = load_state(path)
    mutate(current)
    save_state(path, current)
    return current


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


__all__ = [
    "OPERATOR_KEYS",
    "absorb_operator_edits",
    "load_state",
    "save_state",
    "update_state",
]

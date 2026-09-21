"""Phase-2 occupancy: machine posts land on ``policy.loop_thread`` when set.

The resume root stays conversation + memo. ``wake_ring`` is a different knob
(10532 decoy: ring exists, DIGEST still on the root). Do not treat
``loop_thread`` as ``wake_ring``.
"""

from __future__ import annotations

from typing import Any


def loop_tape_thread(root_id: str, *policies: Any) -> str:
    """Return the occupancy thread: first non-empty ``loop_thread``, else root."""
    rid = str(root_id or "").strip()
    for policy in policies:
        if not isinstance(policy, dict):
            continue
        raw = policy.get("loop_thread")
        if raw is None:
            continue
        tape = str(raw).strip()
        if tape:
            return tape
    return rid

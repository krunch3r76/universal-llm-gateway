"""Wait-for-row-boundary arm resolution (a:37197).

GIW owns the flip: manage passes ``arm=idle|holder:<id>`` on begin-drain so
admits stay open until the named holder's terminal (or true idle). This module
only resolves the arm string from a drain-state snapshot — it does not poll.
"""

from __future__ import annotations

from typing import Any

__all__ = ["resolve_drain_arm"]


def resolve_drain_arm(snapshot: dict[str, Any] | None) -> str:
    """Derive begin-drain ``arm`` from a GIW ``drain_state`` snapshot.

    - no active ops → ``idle`` (activate immediately on the worker)
    - sole active op → ``holder:<op_id>`` (flip on that dispatch's terminal)
    - multiple ops → ``idle`` (admits open until fleet idle; no 2s gap race)
    """
    ops = []
    if isinstance(snapshot, dict):
        raw = snapshot.get("active_ops") or []
        if isinstance(raw, list):
            ops = [o for o in raw if isinstance(o, dict)]
    if not ops:
        return "idle"
    if len(ops) == 1:
        op = ops[0]
        op_id = str(op.get("op_id") or op.get("dispatch_id") or "").strip()
        if op_id:
            return f"holder:{op_id}"
        return "idle"
    return "idle"

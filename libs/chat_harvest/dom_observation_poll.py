"""Readiness polling loops for DOM observation."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from chat_harvest.dom_observation_request import (
    READY_BUDGET_S,
    READY_POLL_S,
    STABLE_BUDGET_S,
    STABLE_POLL_S,
)
from chat_harvest.readonly_cdp import READY_SCRIPT, ReadonlyCDP


def poll_until_turns(
    cdp: ReadonlyCDP,
    conn: Any,
    cfg_ready: dict[str, Any],
    *,
    readiness: dict[str, Any],
    tab_info: dict[str, Any],
    deadline: float,
    clock: Callable[[], float],
    budget_left: Callable[[], float],
) -> dict[str, Any] | None:
    ready_deadline = min(deadline, clock() + READY_BUDGET_S)
    while clock() < ready_deadline and budget_left() > 0:
        readiness["polls"] += 1
        t0 = clock()
        ready_val = cdp.evaluate(conn, READY_SCRIPT, cfg_ready)
        readiness["waited_ms"] += int((clock() - t0) * 1000)
        if isinstance(ready_val, dict):
            tab_info["visibility_state"] = ready_val.get("visibility_state")
            if ready_val.get("login_wall"):
                return {"login_wall": True}
            if int(ready_val.get("turn_nodes") or 0) > 0:
                readiness["turns_seen"] = True
                return None
        time.sleep(READY_POLL_S)
    return None


def poll_until_stable(
    cdp: ReadonlyCDP,
    conn: Any,
    cfg_ready: dict[str, Any],
    *,
    readiness: dict[str, Any],
    deadline: float,
    clock: Callable[[], float],
    budget_left: Callable[[], float],
) -> None:
    stable_deadline = min(deadline, clock() + STABLE_BUDGET_S)
    last_count: int | None = None
    stable_hits = 0
    while clock() < stable_deadline and budget_left() > 0:
        t0 = clock()
        ready_val = cdp.evaluate(conn, READY_SCRIPT, cfg_ready)
        readiness["waited_ms"] += int((clock() - t0) * 1000)
        readiness["polls"] += 1
        if isinstance(ready_val, dict):
            count = int(ready_val.get("element_count") or 0)
            if last_count is not None and count == last_count:
                stable_hits += 1
                if stable_hits >= 3:
                    readiness["stable"] = True
                    return
            else:
                stable_hits = 1
            last_count = count
        time.sleep(STABLE_POLL_S)

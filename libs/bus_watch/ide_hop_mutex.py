"""Single in-flight Glass hop mutex per house root (a:38364 / a:38386 / a:38439).

Re-firing Ctrl+N while a prior keystroke sequence is still settling (or after
SIGTERM mid-wait) double-pastes. Hold a short TTL lock around glass-launch →
land wait so a second fire refuses instead of stacking tabs.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from bus_watch.fable_lock import WATCH_DIR

DEFAULT_TTL_S = 120.0


def hop_mutex_path(root_id: str) -> Path:
    return WATCH_DIR / f"liaison-{root_id}.hop.lock"


def try_acquire_hop_mutex(
    root_id: str,
    *,
    ttl_s: float = DEFAULT_TTL_S,
    now: float | None = None,
) -> dict[str, Any]:
    """Acquire or refuse. ``ok`` false ⇒ another hop is in flight (do not Ctrl+N)."""
    path = hop_mutex_path(root_id)
    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    t = time.time() if now is None else now
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        started = float(raw.get("started_at") or 0.0)
        pid = int(raw.get("pid") or 0)
        age = t - started if started else ttl_s + 1.0
        alive = False
        if pid > 0:
            try:
                os.kill(pid, 0)
                alive = True
            except OSError:
                alive = False
        if age < ttl_s and (alive or started > 0):
            return {
                "ok": False,
                "phase": "hop_mutex_held",
                "path": str(path),
                "holder_pid": pid,
                "age_s": round(age, 2),
                "ttl_s": ttl_s,
                "fix": (
                    f"hop mutex held for root {root_id} (pid={pid}, age={age:.0f}s < "
                    f"{ttl_s:.0f}s TTL) — refuse Ctrl+N re-fire (a:38364/a:38386). "
                    "Poll land / OPERATOR_GATE with message_path; do not glass-launch again."
                ),
            }
    payload = {"root": root_id, "pid": os.getpid(), "started_at": t}
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return {"ok": True, "path": str(path), "pid": payload["pid"], "started_at": t}


def release_hop_mutex(root_id: str, *, pid: int | None = None) -> dict[str, Any]:
    """Drop the lock when this process still owns it (or pid omitted)."""
    path = hop_mutex_path(root_id)
    if not path.is_file():
        return {"ok": True, "released": False, "reason": "absent"}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        path.unlink(missing_ok=True)
        return {"ok": True, "released": True, "reason": "corrupt"}
    holder = int(raw.get("pid") or 0)
    mine = os.getpid() if pid is None else pid
    if holder and holder != mine:
        return {"ok": False, "released": False, "reason": "not_owner", "holder_pid": holder}
    path.unlink(missing_ok=True)
    return {"ok": True, "released": True, "reason": "ok"}

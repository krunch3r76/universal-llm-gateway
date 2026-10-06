"""In-flight Glass hop mutexes — per root and per GUI window (a:38364 / a:38474).

Re-firing Ctrl+N while a prior keystroke sequence is still settling (or after
SIGTERM mid-wait) double-pastes. Hold a TTL lock around glass-launch → land
wait so a second fire refuses instead of stacking tabs.

Per-root lock alone does not serialize two houses into one Agents window
(15441 tip_cp=75 vs 15420 +35s). ``try_acquire_window_mutex`` keys by
``gui_host`` + focus title so concurrent roots cannot drive one toplevel.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from bus_watch.fable_lock import WATCH_DIR

DEFAULT_TTL_S = 120.0
DEFAULT_WINDOW_TTL_S = 600.0
_SAFE = re.compile(r"[^a-zA-Z0-9._-]+")


def hop_mutex_path(root_id: str) -> Path:
    return WATCH_DIR / f"liaison-{root_id}.hop.lock"


def window_mutex_path(gui_host: str, focus_title: str | None) -> Path:
    host = _SAFE.sub("_", (gui_host or "unset").strip()) or "unset"
    title = (focus_title or "Cursor Agents").strip() or "Cursor Agents"
    digest = hashlib.sha256(f"{host}\0{title}".encode()).hexdigest()[:16]
    return WATCH_DIR / f"liaison-gui-{host}-{digest}.hop.lock"


def _try_acquire(
    path: Path,
    *,
    payload_extra: dict[str, Any],
    ttl_s: float,
    phase: str,
    refuse_fix: str,
    now: float | None = None,
) -> dict[str, Any]:
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
                "phase": phase,
                "path": str(path),
                "holder_pid": pid,
                "holder_root": raw.get("root"),
                "age_s": round(age, 2),
                "ttl_s": ttl_s,
                "fix": refuse_fix.format(
                    pid=pid, age=age, ttl_s=ttl_s, holder_root=raw.get("root")
                ),
            }
    payload = {"pid": os.getpid(), "started_at": t, **payload_extra}
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return {"ok": True, "path": str(path), "pid": payload["pid"], "started_at": t}


def _release(path: Path, *, pid: int | None = None) -> dict[str, Any]:
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


def try_acquire_hop_mutex(
    root_id: str,
    *,
    ttl_s: float = DEFAULT_TTL_S,
    now: float | None = None,
) -> dict[str, Any]:
    """Acquire or refuse. ``ok`` false ⇒ another hop is in flight (do not Ctrl+N)."""
    return _try_acquire(
        hop_mutex_path(root_id),
        payload_extra={"root": root_id},
        ttl_s=ttl_s,
        phase="hop_mutex_held",
        refuse_fix=(
            f"hop mutex held for root {root_id} (pid={{pid}}, age={{age:.0f}}s < "
            f"{{ttl_s:.0f}}s TTL) — refuse Ctrl+N re-fire (a:38364/a:38386). "
            "Poll land / OPERATOR_GATE with message_path; do not glass-launch again."
        ),
        now=now,
    )


def release_hop_mutex(root_id: str, *, pid: int | None = None) -> dict[str, Any]:
    """Drop the lock when this process still owns it (or pid omitted)."""
    return _release(hop_mutex_path(root_id), pid=pid)


def try_acquire_window_mutex(
    gui_host: str,
    focus_title: str | None,
    *,
    root_id: str,
    ttl_s: float = DEFAULT_WINDOW_TTL_S,
    now: float | None = None,
) -> dict[str, Any]:
    """Serialize hops from any root into one GUI Agents window (a:38474)."""
    path = window_mutex_path(gui_host, focus_title)
    return _try_acquire(
        path,
        payload_extra={
            "root": root_id,
            "gui_host": gui_host,
            "focus_title": focus_title or "Cursor Agents",
        },
        ttl_s=ttl_s,
        phase="hop_window_mutex_held",
        refuse_fix=(
            "hop window mutex held on {holder_root!s} (pid={pid}, age={age:.0f}s < "
            "{ttl_s:.0f}s TTL) — another root is driving this Agents window "
            "(a:38474). Wait for that hop to land or expire; do not Ctrl+N."
        ),
        now=now,
    )


def release_window_mutex(
    gui_host: str, focus_title: str | None, *, pid: int | None = None
) -> dict[str, Any]:
    return _release(window_mutex_path(gui_host, focus_title), pid=pid)

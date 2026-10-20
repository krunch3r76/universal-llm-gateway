"""Periodic CONTEXT_BUDGET hops for :8 Cursor IDE tabs on endpoint subscriptions."""

from __future__ import annotations

import logging
import re
import subprocess
import sys
import time
from typing import Any

from bus_watch.digest_budget import _utcnow, effective_policy, seat_budget
from bus_watch.endpoint_wake_transport import StoreClient
from bus_watch.fable_lock import WATCH_DIR, bus_watch_repo_root, read_lock
from bus_watch.ide_budget import active_budget_excludes, measure_ide_tab
from bus_watch.tick_state import load_state

logger = logging.getLogger(__name__)

_REPO = bus_watch_repo_root()
_HOP_SCRIPT = _REPO / "scripts" / "liaison-ide-hop.py"
_ROOT_SCOPE_RE = re.compile(r"^\d+$")
_HOP_CHECK_INTERVAL_S = 60.0
_HOP_COOLDOWN_S = 300.0


def _numeric_root_subscriptions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("surface") or "") != "cursor":
            continue
        scope = str(row.get("thread_scope") or "").strip()
        if not _ROOT_SCOPE_RE.match(scope):
            continue
        disp = str(row.get("display") or "").strip()
        if disp not in ("8", ":8", "orion-node"):
            continue
        out.append(row)
    return out


def _cooldown_key(root_id: str, transcript_id: str) -> str:
    return f"hop:{root_id}:{transcript_id}"


def _root_due(root_id: str, mark: dict[str, float], *, now: float) -> bool:
    last = mark.get(f"check:{root_id}", 0.0)
    return (now - last) >= _HOP_CHECK_INTERVAL_S


def _transcript_cooled(
    root_id: str, transcript_id: str, mark: dict[str, float], *, now: float
) -> bool:
    last = mark.get(_cooldown_key(root_id, transcript_id), 0.0)
    return (now - last) >= _HOP_COOLDOWN_S


def _budget_for_root(root_id: str) -> dict[str, Any] | None:
    state_path = WATCH_DIR / f"liaison-{root_id}.tick.json"
    state = load_state(state_path)
    policy = effective_policy(state)
    lock = read_lock(root_id)
    register = str(state.get("register") or "attended")
    measure = measure_ide_tab(root_id, lock, policy)
    if not measure:
        return None
    budget, _pct = seat_budget(
        est=0,
        successor_model=str(policy.get("successor_model") or policy.get("ide_model") or ""),
        usage_live=None,
        holder_dispatch="",
        register=register,
        lock=lock,
        policy=policy,
        root_id=root_id,
        digest_ts=_utcnow(),
        epoch=str(measure.get("transcript_id") or ""),
    )
    return budget


def _run_ide_hop(
    root_id: str, transcript_id: str, *, gui_host: str = ":8"
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(_HOP_SCRIPT),
            "--root",
            root_id,
            "--transcript-id",
            transcript_id,
            "--gui-host",
            gui_host,
            "--force",
        ],
        cwd=str(_REPO),
        timeout=180,
        capture_output=True,
        text=True,
    )


def check_cursor_ide_context_hops(
    store: StoreClient,
    *,
    mark: dict[str, float] | None = None,
    now: float | None = None,
) -> None:
    """Every 60s per root, hop idle :8 Cursor tabs that hit CONTEXT_BUDGET."""
    hop_mark = mark if mark is not None else {}
    mono = time.monotonic() if now is None else now
    try:
        rows = store.subscriptions("cursor")
    except Exception:
        logger.exception("ide_context_hop_monitor: subscription list failed")
        return
    for sub in _numeric_root_subscriptions(rows):
        root_id = str(sub.get("thread_scope") or "")
        if not _root_due(root_id, hop_mark, now=mono):
            continue
        hop_mark[f"check:{root_id}"] = mono
        try:
            budget = _budget_for_root(root_id)
        except Exception:
            logger.exception(
                "ide_context_hop_monitor: budget failed root=%s", root_id
            )
            continue
        if not budget or budget.get("stop_class") != "CONTEXT_BUDGET":
            continue
        if budget.get("glass_status") == "generating":
            continue
        transcript_id = str(budget.get("transcript_id") or "")
        if not transcript_id:
            continue
        excluded = active_budget_excludes(root_id)
        if transcript_id in excluded:
            continue
        if not _transcript_cooled(root_id, transcript_id, hop_mark, now=mono):
            continue
        hop_mark[_cooldown_key(root_id, transcript_id)] = mono
        sub_display = str(sub.get("display") or ":8").strip() or ":8"
        gui_host = sub_display if sub_display.startswith(":") else sub_display
        if gui_host == "8":
            gui_host = ":8"
        try:
            proc = _run_ide_hop(root_id, transcript_id, gui_host=gui_host)
        except subprocess.TimeoutExpired:
            logger.warning(
                "ide_context_hop_monitor: hop timeout root=%s transcript=%s",
                root_id,
                transcript_id,
            )
            continue
        except Exception:
            logger.exception(
                "ide_context_hop_monitor: hop spawn failed root=%s", root_id
            )
            continue
        if proc.returncode == 0:
            logger.info(
                "ide_context_hop_monitor: hop landed root=%s transcript=%s",
                root_id,
                transcript_id,
            )
        else:
            logger.warning(
                "ide_context_hop_monitor: hop exit=%s root=%s transcript=%s stderr=%s",
                proc.returncode,
                root_id,
                transcript_id,
                (proc.stderr or proc.stdout or "")[:400],
            )


__all__ = ["check_cursor_ide_context_hops"]

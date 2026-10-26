"""``go under`` — hand an attended liaison house to the gear-3 ticker in one verb.

The four-step prose sequence (register autonomous · ``ready`` · release the
``ide:`` seat · one-line UNDER reply) never ran on 10534 (2026-09-13 06:11Z):
the tab's model read ``ready`` as an operator gate, wrote "restore cursor-sdk
successor hop" into its CHECKPOINT and parked at 99.6 % of its window with four
unread closeouts. Here the harness owns the sequence, so the guarantee is
structural: after this call the house is ``autonomous`` and ``policy.ready`` is
true (the ticker is the driver — an IDE-hop chain keeps ``ready=false``), the
``ide:`` seat is free, no attended ``--loop`` is left waking the old tab, a
ticker lease is live, and the next ticker poll carries one handoff wake
(``spawn_pending.handoff_wake``). The CHECKPOINT stays with the seat — it
authors the residue — and ``--mark-checkpoint`` rides in the same call.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_HUB_ROOT = Path("/mnt/torus/projects/universal-llm-gateway")

from bus_watch.digest_budget import effective_policy
from bus_watch.fable_lock import (
    WATCH_DIR,
    read_lock,
    read_ticker_lock,
    release_fable_lock,
    ticker_lease_live,
)
from bus_watch.loop_tape_mint import ensure_loop_tape
from bus_watch.spawn_wake.play_classify import MODE_AWARE, MODE_SIT, plant_play_state
from bus_watch.tick_state import save_state


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def attended_loop_pids(root: str) -> list[int]:
    """PIDs of ``liaison-tick.py --root <root> --loop`` that are not the ticker."""
    import psutil

    out: list[int] = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        argv = proc.info.get("cmdline") or []
        if proc.info["pid"] == os.getpid() or "--spawn-on-wake" in argv:
            continue
        joined = " ".join(argv)
        if "liaison-tick.py" in joined and "--loop" in argv and _root_of(argv) == root:
            out.append(int(proc.info["pid"]))
    return out


def _root_of(argv: list[str]) -> str | None:
    for i, tok in enumerate(argv):
        if tok == "--root" and i + 1 < len(argv):
            return argv[i + 1]
        if tok.startswith("--root="):
            return tok.split("=", 1)[1]
    return None


def stop_attended_loops(root: str) -> list[int]:
    """SIGTERM the old tab's loops; each exits through its own ``finally`` and
    releases the seat it claimed, so the lease semantics stay with the claimer."""
    pids = attended_loop_pids(root)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
    return pids


def ticker_alive(root: str) -> bool:
    return ticker_lease_live(root_id=root)


def _ticker_unit_name(root: str) -> str:
    return f"liaison-ticker-{root}.service"


def _user_manager_reachable() -> bool:
    probe = subprocess.run(  # noqa: S603
        ["systemctl", "--user", "show", "--property=Version", "--value"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if probe.returncode != 0:
        return False
    return bool((probe.stdout or "").strip())


def start_ticker(root: str, *, log_dir: Path = WATCH_DIR) -> dict[str, Any]:  # noqa: ARG001
    """Start gear-3 ticker via user systemd (installed unit or transient)."""
    _ = log_dir
    unit = _ticker_unit_name(root)
    if not _user_manager_reachable():
        return {
            "started": False,
            "pid": None,
            "supervisor": None,
            "reason": "user_manager_unreachable",
        }
    load = subprocess.run(  # noqa: S603
        ["systemctl", "--user", "show", "-p", "LoadState", "--value", unit],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if load.returncode != 0:
        return {
            "started": False,
            "pid": None,
            "supervisor": None,
            "reason": "user_manager_unreachable",
        }
    load_state = (load.stdout or "").strip()
    hub = _HUB_ROOT
    if load_state == "loaded":
        start = subprocess.run(  # noqa: S603
            ["systemctl", "--user", "start", unit],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if start.returncode != 0:
            return {
                "started": False,
                "pid": None,
                "supervisor": None,
                "reason": "ticker_unit_start_failed",
            }
    else:
        child = [
            sys.executable,
            "scripts/liaison-tick.py",
            "--root",
            root,
            "--loop",
            "--spawn-on-wake",
        ]
        cmd = [
            "systemd-run",
            "--user",
            f"--unit=liaison-ticker-{root}",
            "--collect",
            f"--working-directory={hub}",
            "--setenv",
            f"PYTHONPATH={hub}/libs",
            "--",
            *child,
        ]
        run = subprocess.run(  # noqa: S603
            cmd, capture_output=True, text=True, timeout=30, check=False
        )
        if run.returncode != 0:
            return {
                "started": False,
                "pid": None,
                "supervisor": None,
                "reason": "ticker_unit_start_failed",
            }
    return {
        "started": True,
        "pid": None,
        "log": None,
        "supervisor": "systemd",
        "unit": unit,
    }


def ensure_ticker(
    root: str, *, start: Callable[[str], dict[str, Any]] = start_ticker
) -> dict[str, Any]:
    if ticker_alive(root):
        return {"started": False, "alive": True, "lock": read_ticker_lock(root)}
    return {"alive": False, **start(root)}


def under_line(root: str, policy: dict[str, Any]) -> str:
    """The one-line reply the tab pastes before ending its turn."""
    play = policy.get("play") or MODE_AWARE
    return (
        f"UNDER → liaison-ticker-{root} · successor_model={policy.get('successor_model')} "
        f"· leftover={play} · spawns hold|play|sit · check-in: resume {root} · come up: I'm back"
    )


def go_under(
    root: str,
    state: dict[str, Any],
    *,
    state_path: Path,
    holder: str = "",
    leftover_mode: str = MODE_AWARE,
    stop_loops: Callable[[str], list[int]] = stop_attended_loops,
    ensure: Callable[[str], dict[str, Any]] = ensure_ticker,
    release: Callable[..., dict[str, Any]] = release_fable_lock,
    mint: Callable[..., str | None] = ensure_loop_tape,
) -> dict[str, Any]:
    """Flip, persist, free the seat, retire the tab's loops, guarantee a ticker.

    ``holder`` names the tab (``ide:<transcript_id>``); when omitted any ``ide:``
    holder is released — the verb is the operator's or the tab's own word for
    the attended seat. A live ``sdk:`` holder is left alone (the pending-spawn
    mutex already serialises successors) and reported.
    """
    previous = str(state.get("register") or "attended")
    state["register"] = "autonomous"
    policy = dict(state.get("policy") or {})
    ready_before = policy.get("ready")
    policy["ready"] = True  # go under = choose the ticker as the driver
    # Fresh roots inherit POLICY_DEFAULTS gear 1 (spawn disabled, post_digest
    # off). 11667 2026-09-18 went under with only successor_model set; the
    # ticker saw attention including the new child and logged spawn.action=
    # disabled. Gear 3 is the overnight driver; do not clobber an explicit gear.
    if "gear" not in policy:
        policy["gear"] = "3-wake-on-attention"
    model = str(policy.get("successor_model") or "")
    if model.startswith("cdp/") and "successor_seat" not in policy:
        policy["successor_seat"] = "cdp"
    state["policy"] = policy
    # Classifier default — never successor_contract=conductor as the play path.
    mode = MODE_SIT if leftover_mode == MODE_SIT else MODE_AWARE
    planted = plant_play_state(state, mode=mode, as_of=_utcnow())
    policy = dict(state.get("policy") or {})
    seq = int((state.get("handoff") or {}).get("seq") or 0) + 1
    state["handoff"] = {
        "seq": seq,
        "requested_at": _utcnow(),
        "holder": holder or None,
        "from_register": previous,
    }
    child = mint(root, state, state_path)
    if child:
        policy = dict(state.get("policy") or {})
        policy["loop_thread"] = child
        state["policy"] = policy
    save_state(state_path, state)
    result: dict[str, Any] = {
        "ok": True,
        "root": root,
        "register": {"from": previous, "to": "autonomous"},
        "handoff_seq": seq,
        "ready": {"before": ready_before, "after": True},
        "play": planted,
        "state": str(state_path),
    }
    lock = read_lock(root)
    held = str(lock.get("holder") or "")
    if held.startswith("ide:") and (not holder or held == holder):
        result["seat_release"] = release(held, pid=None, root_id=root)
    elif held:
        result["seat_release"] = {
            "ok": False,
            "reason": "not_ide_holder",
            "holder": held,
        }
    else:
        result["seat_release"] = {"ok": True, "reason": "already_free"}
    result["stopped_loops"] = stop_loops(root)
    result["ticker"] = ensure(root)
    ticker = result["ticker"]
    if not ticker.get("started") and ticker.get("alive") is not True:
        result["ok"] = False
        if "refused" not in result:
            result["refused"] = ticker.get("reason") or "ticker_not_started"
    effective = effective_policy(state)
    result["armed"] = bool(effective.get("ready")) and bool(
        effective.get("successor_model")
    )
    if not effective.get("successor_model"):
        result["ok"] = False
        result["refused"] = "successor_model_unset — --set successor_model=<slug> first"
    result["under_line"] = under_line(root, effective)
    return result


__all__ = [
    "attended_loop_pids",
    "ensure_ticker",
    "go_under",
    "start_ticker",
    "stop_attended_loops",
    "ticker_alive",
    "under_line",
]

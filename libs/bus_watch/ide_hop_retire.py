"""Tear down the departing IDE tab's harness after a hop lands.

``ok`` on ``fire_ide_hop`` used to leave the old tab's ``--loop``,
``watch-supervise`` tails, and ``ide:`` lock live. Cursor then kept
waking that tab when a native goal was still active. Specimen: 11912 hop
2026-09-21 — land ``7484bed2-…``, loop killed by hand, leftover goal ran
5m45s. Pickup no longer mints ``CreateGoal`` (no interval; continuation
inject is not the house ticker). If a leftover goal is still active,
``UpdateGoal(complete)`` stops that inject.

Pollers survive retire; loop and tab tails die. The successor ARM line is
``tail`` attach-only. ``--forever`` debug tails stay. Heartbeat backup is 1200s.
"""

from __future__ import annotations

import json
import os
import signal
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import psutil

from bus_watch.fable_lock import HOUSE_LABEL_PREFIX, WATCH_DIR, release_fable_lock
from bus_watch.go_under import stop_attended_loops

GOAL_RELEASE = (
    "LIAISON_HOP_TAB_GOAL_RELEASE: If a native Cursor goal is active on this tab, "
    'CallDynamicTool(namespace="cursor", toolName="UpdateGoal", arguments={"status":"complete"}) '
    "after liaison-ide-hop.py ok, before the RETIRED line — leftover continuation-wake only "
    "(11912 / 12088). Successor skips CreateGoal; attaches `tail --label` per ARM label + "
    "`liaison-tick.py --loop --heartbeat 1200` "
    "(20 min re-arm / watcher health)."
)


def departing_watcher_labels(root_id: str, watch_dir: Path = WATCH_DIR) -> list[str]:
    """Every stem that belongs to ``root_id`` — tails may still be attached
    after the poller has gone terminal."""
    labels: set[str] = set()
    for path in sorted(watch_dir.glob("*.state.json")):
        stem = path.name.removesuffix(".state.json")
        if stem.startswith(HOUSE_LABEL_PREFIX):
            continue
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
        thread = ""
        if isinstance(state, dict):
            thread = str(state.get("thread") or "")
        if stem.startswith(f"{root_id}-") or thread == str(root_id):
            labels.add(stem)
    return sorted(labels)


def _is_supervise_tail(argv: list[str]) -> bool:
    joined = " ".join(argv)
    return "watch-supervise.sh" in joined and "tail" in argv and "--forever" not in argv


def _label_from_argv(argv: list[str]) -> str | None:
    for i, tok in enumerate(argv):
        if tok == "--label" and i + 1 < len(argv):
            return argv[i + 1]
        if tok.startswith("--label="):
            return tok.split("=", 1)[1]
    return None


def departing_tail_pids(root_id: str, labels: Iterable[str]) -> list[int]:
    wanted = set(labels)
    prefix = f"{root_id}-"
    house = f"{HOUSE_LABEL_PREFIX}{root_id}-"
    out: list[int] = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        argv = proc.info.get("cmdline") or []
        if not _is_supervise_tail(argv):
            continue
        label = _label_from_argv(argv)
        if label and (
            label in wanted
            or label.startswith(prefix)
            or label.startswith(house)
        ):
            out.append(int(proc.info["pid"]))
    return out


def stop_departing_tails(
    root_id: str,
    labels: Iterable[str],
    *,
    pids_for: Callable[[str, Iterable[str]], list[int]] | None = None,
) -> list[int]:
    """SIGTERM ``watch-supervise.sh tail --label L`` for this root."""
    finder = pids_for or departing_tail_pids
    pids = finder(root_id, labels)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
    return pids


def retire_departing_tab(
    root: str,
    holder: str,
    *,
    watch_dir: Path = WATCH_DIR,
    stop_loops: Callable[[str], list[int]] = stop_attended_loops,
    labels_for: Callable[..., list[str]] = departing_watcher_labels,
    stop_tails: Callable[..., list[int]] = stop_departing_tails,
    release: Callable[..., dict[str, Any]] = release_fable_lock,
) -> dict[str, Any]:
    """Kill this tab's loop + tab tails and free the ``ide:`` seat.

    House pollers survive; does not UpdateGoal.
    """
    labels = labels_for(root, watch_dir)
    result: dict[str, Any] = {
        "ok": True,
        "root": root,
        "stopped_loops": stop_loops(root),
        "labels": labels,
        "stopped_tails": stop_tails(root, labels),
        "goal": GOAL_RELEASE,
    }
    if holder.startswith("ide:"):
        result["seat_release"] = release(holder, pid=None, root_id=root)
    else:
        result["seat_release"] = {"ok": False, "reason": "holder_not_ide"}
    return result


__all__ = [
    "GOAL_RELEASE",
    "departing_tail_pids",
    "departing_watcher_labels",
    "retire_departing_tab",
    "stop_departing_tails",
]

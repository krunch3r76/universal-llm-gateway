"""Tear down the departing IDE tab's harness after a hop lands.

``ok`` on ``fire_ide_hop`` used to leave the old tab's ``--loop``,
``watch-supervise`` tails, and ``ide:`` lock live. Cursor then kept
waking that tab when a native goal was still active. Specimen: 11912 hop
2026-09-21 — land ``7484bed2-…``, loop killed by hand, leftover goal ran
5m45s. Pickup no longer mints ``CreateGoal`` (no interval; continuation
inject is not the house ticker). If a leftover goal is still active,
``UpdateGoal(complete)`` stops that inject.

Pollers tear down with the tails and loop: the successor ARM line is
``rebuild`` (stop leftover → start from ``tmp/watchers/<label>.argv.json``
→ tail). ``--forever`` debug tails stay. Heartbeat backup is 1200s.
"""

from __future__ import annotations

import json
import os
import signal
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import psutil

from bus_watch.fable_lock import WATCH_DIR, release_fable_lock
from bus_watch.go_under import stop_attended_loops

GOAL_RELEASE = (
    "LIAISON_HOP_TAB_GOAL_RELEASE: If a native Cursor goal is active on this tab, "
    'CallDynamicTool(namespace="cursor", toolName="UpdateGoal", arguments={"status":"complete"}) '
    "after liaison-ide-hop.py ok, before the RETIRED line — leftover continuation-wake only "
    "(11912 / 12088). Successor skips CreateGoal; rebuilds watcher start+tail from "
    "`tmp/watchers/<label>.argv.json` + `liaison-tick.py --loop --heartbeat 1200` "
    "(20 min re-arm / watcher health)."
)


def departing_watcher_labels(root_id: str, watch_dir: Path = WATCH_DIR) -> list[str]:
    """Every stem that belongs to ``root_id`` — tails may still be attached
    after the poller has gone terminal."""
    labels: set[str] = set()
    for path in sorted(watch_dir.glob("*.state.json")):
        stem = path.name.removesuffix(".state.json")
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


def _is_supervise_start(argv: list[str]) -> bool:
    joined = " ".join(argv)
    return "watch-supervise.sh" in joined and "start" in argv and "tail" not in argv


def _is_poller_argv(argv: list[str]) -> bool:
    """The detached watcher (supervise start wrapper or the python after ``--``)."""
    if _is_supervise_tail(argv):
        return False
    joined = " ".join(argv)
    if _is_supervise_start(argv):
        return True
    return "watch-bus-consult" in joined


def _label_from_argv(argv: list[str]) -> str | None:
    for i, tok in enumerate(argv):
        if tok == "--label" and i + 1 < len(argv):
            return argv[i + 1]
        if tok.startswith("--label="):
            return tok.split("=", 1)[1]
    return None


def watcher_argv_from_cmdline(argv: list[str]) -> list[str]:
    """Strip the supervise wrapper so the successor can ``start -- <argv>``."""
    if _is_supervise_start(argv) and "--" in argv:
        return argv[argv.index("--") + 1 :]
    return list(argv)


def argv_path(label: str, watch_dir: Path = WATCH_DIR) -> Path:
    return watch_dir / f"{label}.argv.json"


def write_rebuild_argv(
    label: str, argv: list[str], watch_dir: Path = WATCH_DIR
) -> Path:
    path = argv_path(label, watch_dir)
    path.write_text(
        json.dumps(watcher_argv_from_cmdline(argv)), encoding="utf-8"
    )
    return path


def _pid_from_label(label: str, watch_dir: Path) -> int | None:
    pid_path = watch_dir / f"{label}.pid"
    if pid_path.is_file():
        try:
            text = pid_path.read_text(encoding="utf-8").strip()
        except OSError:
            text = ""
        if text:
            try:
                return int(text)
            except ValueError:
                pass
    state_path = watch_dir / f"{label}.state.json"
    if not state_path.is_file():
        return None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(state, dict):
        return None
    try:
        return int(state.get("pid"))
    except (TypeError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _cmdline(pid: int) -> list[str]:
    try:
        return list(psutil.Process(pid).cmdline())
    except (psutil.Error, ProcessLookupError):
        return []


def departing_tail_pids(root_id: str, labels: Iterable[str]) -> list[int]:
    wanted = set(labels)
    prefix = f"{root_id}-"
    out: list[int] = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        argv = proc.info.get("cmdline") or []
        if not _is_supervise_tail(argv):
            continue
        label = _label_from_argv(argv)
        if label and (label in wanted or label.startswith(prefix)):
            out.append(int(proc.info["pid"]))
    return out


def departing_poller_pids(
    root_id: str,
    labels: Iterable[str],
    watch_dir: Path = WATCH_DIR,
) -> dict[int, str]:
    """Live poller pid → label. Pid files first; process scan fills gaps."""
    wanted = set(labels)
    prefix = f"{root_id}-"
    found: dict[int, str] = {}
    for label in wanted:
        pid = _pid_from_label(label, watch_dir)
        if pid is not None and _pid_alive(pid):
            found[pid] = label
    for proc in psutil.process_iter(["pid", "cmdline"]):
        argv = proc.info.get("cmdline") or []
        if not _is_poller_argv(argv):
            continue
        label = _label_from_argv(argv)
        if not label:
            continue
        if label in wanted or label.startswith(prefix):
            found[int(proc.info["pid"])] = label
    return found


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


def stop_departing_pollers(
    root_id: str,
    labels: Iterable[str],
    *,
    watch_dir: Path = WATCH_DIR,
    pids_for: Callable[..., dict[int, str]] | None = None,
) -> list[int]:
    """Snapshot start argv, then SIGTERM the detached poller so the successor rebuilds."""
    finder = pids_for or departing_poller_pids
    found = finder(root_id, labels, watch_dir)
    stopped: list[int] = []
    for pid, label in found.items():
        argv = _cmdline(pid)
        if argv:
            write_rebuild_argv(label, argv, watch_dir)
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        stopped.append(pid)
    return stopped


def retire_departing_tab(
    root: str,
    holder: str,
    *,
    watch_dir: Path = WATCH_DIR,
    stop_loops: Callable[[str], list[int]] = stop_attended_loops,
    labels_for: Callable[..., list[str]] = departing_watcher_labels,
    stop_tails: Callable[..., list[int]] = stop_departing_tails,
    stop_pollers: Callable[..., list[int]] = stop_departing_pollers,
    release: Callable[..., dict[str, Any]] = release_fable_lock,
) -> dict[str, Any]:
    """Kill this tab's loop + pollers + tails and free the ``ide:`` seat.

    Snapshots poller argv to ``<label>.argv.json`` before SIGTERM so the
    successor can ``start --`` the same command. Does not UpdateGoal.
    """
    labels = labels_for(root, watch_dir)
    result: dict[str, Any] = {
        "ok": True,
        "root": root,
        "stopped_loops": stop_loops(root),
        "labels": labels,
        "stopped_pollers": stop_pollers(root, labels, watch_dir=watch_dir),
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
    "argv_path",
    "departing_poller_pids",
    "departing_tail_pids",
    "departing_watcher_labels",
    "retire_departing_tab",
    "stop_departing_pollers",
    "stop_departing_tails",
    "watcher_argv_from_cmdline",
    "write_rebuild_argv",
]

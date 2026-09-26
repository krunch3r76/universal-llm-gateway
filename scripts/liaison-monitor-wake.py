#!/usr/bin/env python3
"""Wake or hop the liaison-monitor Cursor tab on orion-node.

``hop`` opens a new chat (Ctrl+n) and pastes ``resume <root>``. ``wake`` pastes
into the focused chat, and only when the watched house has no live conductor.
That wake does not say resume. It tells the tab to hop if its context is dense.
``loop`` checks every ``--interval-minutes`` (default 5). It pastes only when no
conductor is live. Every ``--safeguard-minutes`` (default 60) it pastes anyway,
so a runaway of admitted conductors cannot stay silent.
The test interval is 5 minutes; overnight is ``--interval-minutes 20``.
A quiet NOW is not the program ending. The loop does not exit on it. The seat
kills the process only when the continuity card's stop is met.

The keystrokes run on the GUI host over SSH (``orchestrator_tab_keystroke.py``).
This does not stop the house ticker.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

from bus_watch.ide_hop import OPERATOR_LOOP, session_unreachable

_REPO = Path(__file__).resolve().parents[1]
_WATCHED_HOUSE = "12586"
_DEFAULT_ROOT = "12606"
_DEFAULT_GUI = "orion-node"
_TICK = _REPO / "tmp" / "watchers" / f"liaison-{_WATCHED_HOUSE}.tick.json"
_MSG_DIR = _REPO / "tmp" / "watchers" / "handoff-messages"
_LIVE_LIFECYCLES = frozenset({"admitted", "running", "in_flight", "hopping"})
# Test cadence. Overnight: --interval-minutes 20.
_TEST_INTERVAL_MINUTES = 5
_SAFECARD_MINUTES = 60


def _card_uri(root_id: str) -> str:
    return f"cortex://notes/system/threads/{root_id}-continuity.md"


def _standing_tail(root_id: str) -> str:
    """Last lines of a paste. They outrank a quiet-NOW closeout."""
    return (
        f"Standing order: {_card_uri(root_id)}. "
        "A quiet NOW or one landed row is not the program ending. "
        "Do not kill this loop, and do not set NOW to quiet, "
        "unless that card's stop is met. "
        "Do not end the turn on a hold. Act until a different move can be hired. "
        f"{OPERATOR_LOOP}\n"
    )


def hop_message(root_id: str) -> str:
    """First message of a new chat. Starts with resume so the fence fires."""
    return (
        f"resume {root_id}\n\n"
        f"This chat monitors house {_WATCHED_HOUSE} liaison-unattended-6h. "
        f"Read {_card_uri(root_id)} before acting.\n"
        "Service the house and the harness only when its conductors have stopped. "
        "If this tab is dense, checkpoint this root before any further hop.\n"
        f"{_standing_tail(root_id)}"
    )


def safeguard_message(root_id: str, detail: str) -> str:
    """Hourly paste, including while conductors are live."""
    now = ""
    cap = ""
    if _TICK.is_file():
        try:
            policy = json.loads(_TICK.read_text(encoding="utf-8")).get("policy") or {}
            now = str(policy.get("now_row") or "")
            cap = str(policy.get("max_conductors") or 2)
        except json.JSONDecodeError:
            now = "unreadable"
    return (
        f"Safeguard for house {_WATCHED_HOUSE} liaison-unattended-6h. {detail}. "
        f"NOW: {now}. max_conductors: {cap}. "
        "If more conductors are admitted than that cap, or the same pin is being "
        "re-admitted, fix the harness. "
        f"If this tab is dense, checkpoint {root_id} and hop "
        f"(scripts/liaison-monitor-wake.py hop --root {root_id} "
        "--transcript-id <this-tab-uuid>) before that edit.\n"
        f"{_standing_tail(root_id)}"
    )


def wake_message(root_id: str) -> str:
    """Paste into the already-open tab. Does not resume in place."""
    return (
        f"Conductors on house {_WATCHED_HOUSE} liaison-unattended-6h are stopped. "
        "If this tab's context is dense, do not service here: checkpoint "
        f"{root_id} and hop (scripts/liaison-monitor-wake.py hop --root {root_id} "
        "--transcript-id <this-tab-uuid>). The hop seals the checkpoint first. "
        "If this tab is still thin, service the house and the harness.\n"
        f"{_standing_tail(root_id)}"
    )


def conductors_stopped() -> tuple[bool, str]:
    """True when the watched house has no conductor in a live lifecycle."""
    if not _TICK.is_file():
        return True, "no tick file"
    try:
        data = json.loads(_TICK.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return True, "tick file unreadable"
    raw = data.get("digest_lanes_fp") or ""
    try:
        lanes = json.loads(raw).get("lanes") if isinstance(raw, str) else []
    except json.JSONDecodeError:
        lanes = []
    live = [
        str(row[0])
        for row in lanes
        if isinstance(row, list) and len(row) >= 4 and str(row[3]) in _LIVE_LIFECYCLES
    ]
    if live:
        return False, "live=" + ",".join(live)
    return True, "no live conductor"


def house_fully_played() -> bool:
    """False. A quiet NOW is not the minted program ending.

    The seat kills this process only when the continuity card's stop is met.
    The tick file cannot see that, so the loop does not exit on its own.
    """
    return False


def _remote_cmd(op: str, remote_msg: str, repo: str) -> str:
    # No tab title. Pick the IDE window whose title contains the repo name,
    # then type into the chat that window already has focused.
    focus = "--no-raise --focus-app-id cursor"
    return (
        "export WAYLAND_DISPLAY=wayland-1 XDG_RUNTIME_DIR=/run/user/1000; "
        f"python3 {repo}/scripts/orchestrator_tab_keystroke.py {op} "
        f"--message-file {remote_msg} --repo {repo} {focus}"
    )


def keystroke(
    op: str,
    message: str,
    *,
    root_id: str,
    gui_host: str,
) -> dict[str, object]:
    """SSH ``op`` (``launch`` or ``followup``) to the GUI host."""
    locked = session_unreachable(gui_host)
    if locked is not None:
        return {"ok": False, "phase": "session_locked", **locked}
    _MSG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path = _MSG_DIR / f"monitor-{root_id}-{op}-{stamp}.md"
    path.write_text(message, encoding="utf-8")
    remote_msg = f"{_REPO}/{path.relative_to(_REPO)}"
    cmd = _remote_cmd(op, remote_msg, str(_REPO))
    try:
        proc = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", gui_host, cmd],
            capture_output=True,
            text=True,
            timeout=90,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "phase": "ssh_timeout", "message_path": str(path)}
    ok = proc.returncode == 0
    return {
        "ok": ok,
        "phase": "sent" if ok else "keystroke",
        "returncode": proc.returncode,
        "stdout": proc.stdout[-800:],
        "stderr": proc.stderr[-800:],
        "message_path": str(path),
        "gui_host": gui_host,
    }


def seal_then_hop(root_id: str, gui_host: str, transcript_id: str) -> dict[str, object]:
    """Checkpoint the departing tab, then open the successor chat.

    Keystrokes are refused when the seal does not land. The house ticker is
    not stopped.
    """
    from bus_watch.ide_hop import seal_hop_window

    sealed = seal_hop_window(root_id, transcript_id=transcript_id)
    if not sealed.get("ok"):
        return {"ok": False, "phase": "checkpoint", "seal": sealed}
    sent = keystroke(
        "launch",
        hop_message(root_id),
        root_id=root_id,
        gui_host=gui_host,
    )
    sent["seal"] = sealed
    return sent


def wake_once(root_id: str, gui_host: str, *, force: bool = False) -> dict[str, object]:
    """Paste into the focused chat. ``force`` pastes even when conductors are live."""
    stopped, detail = conductors_stopped()
    if not stopped and not force:
        return {"ok": True, "skipped": True, "reason": detail}
    text = wake_message(root_id) if stopped else safeguard_message(root_id, detail)
    result = keystroke("followup", text, root_id=root_id, gui_host=gui_host)
    result["reason"] = detail
    result["forced"] = force and not stopped
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cmd", choices=("hop", "wake", "loop"))
    parser.add_argument("--root", default=_DEFAULT_ROOT)
    parser.add_argument("--gui-host", default=_DEFAULT_GUI)
    parser.add_argument(
        "--interval-minutes",
        type=float,
        default=_TEST_INTERVAL_MINUTES,
        help="how often to check; paste only when no conductor is live",
    )
    parser.add_argument(
        "--safeguard-minutes",
        type=float,
        default=_SAFECARD_MINUTES,
        help="force a wake this often even while conductors are live",
    )
    parser.add_argument(
        "--transcript-id",
        default="",
        help="Departing Cursor tab UUID. Required for hop, so the checkpoint seals first.",
    )
    args = parser.parse_args()
    if args.cmd == "hop":
        if not args.transcript_id:
            print(json.dumps({"ok": False, "phase": "transcript_id_required"}))
            return 1
        result = seal_then_hop(args.root, args.gui_host, args.transcript_id)
    elif args.cmd == "wake":
        result = wake_once(args.root, args.gui_host)
    else:
        last_force = time.time()
        safeguard_s = max(args.safeguard_minutes, 0.1) * 60
        while True:
            time.sleep(max(args.interval_minutes, 0.1) * 60)
            if house_fully_played():
                print(json.dumps({"ok": True, "stopped": True, "reason": "house_fully_played"}))
                return 0
            force = (time.time() - last_force) >= safeguard_s
            result = wake_once(args.root, args.gui_host, force=force)
            if result.get("phase") == "sent":
                last_force = time.time()
            print(json.dumps(result), flush=True)
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

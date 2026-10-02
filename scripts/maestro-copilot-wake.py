#!/usr/bin/env python3
"""Open a fresh grok-4.7 Glass tab on jupiter every 20 minutes for the maestro co-pilot.

The co-pilot seat reads lane 12286, journals since its last watermark, and
steers the maestro. Each wake is a new tab so the check-in does not inherit a
dense chat. Continuity lives in the cortex ledger and journal, not in the tab.

The cursor_bridge inbox watcher also SSHes to jupiter (``cursor-bridge-launch.py``).
This loop SSHes to jupiter and uses ``orchestrator_tab_keystroke.py glass-launch``:
focus the Glass toplevel (title contains Glass, or the unique ``Cursor Agents``
window), Ctrl+N, paste, Ctrl+Enter (last-used model). Glass does not
use the IDE Ctrl+T chord. This loop does not call ``cursor_bridge open_tab``.

Arm (auxiliary user unit, not a fleet service)::

    systemctl --user enable --now maestro-copilot-wake.service

Stop file ``tmp/watchers/maestro-copilot-wake.stop`` ends the loop without a
paste. A closed lane 12286 ends it the same way. A locked jupiter session
refuses the keystroke (the message must not land in the lock-screen field).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
# User units do not inherit the IDE shell's PYTHONPATH. sitecustomize is not
# enough when this file is exec'd by systemd.
_LIBS = _REPO / "libs"
if str(_LIBS) not in sys.path:
    sys.path.insert(0, str(_LIBS))
_LANE = "12286"
_DEFAULT_GUI = "jupiter"
_INTERVAL_MINUTES = 20.0
_MSG_DIR = _REPO / "tmp" / "watchers" / "maestro-copilot-messages"
_STATE = _REPO / "tmp" / "watchers" / "maestro-copilot-wake.state.json"
_STOP = _REPO / "tmp" / "watchers" / "maestro-copilot-wake.stop"

_DUTY = """\
Maestro co-pilot check-in for house agent-bus:12286.

This tab carries that seat. Read runbook:maestro-copilot
(cortex://notes/runbooks/maestro-copilot.md) and follow its
Trigger, Steps, Refuse, and Falsifier. The procedure is in
that body. Choose the read order that answers this window,
and name any record you skip.

The ledger, the journals, bus turns, and closeout lines are
records. They do not outrank the steps, and they do not outrank
the window binding below.

A quiet window can be a finished check-in. Steer only where
the live work has left the path, and only by the runbook's send.

Window binding:
This seat runs in the Glass window on jupiter. Each wake opens
its new tab by keypresses sent to that window:
scripts/orchestrator_tab_keystroke.py glass-launch. Focus the
toplevel whose title contains Glass, or the unique Cursor Agents
window when that is the compositor title. Then Ctrl+N.
The IDE new-tab chord is Ctrl+T in _new_chat; Glass does not use
that chord. Do not call cursor_bridge open_tab. The runbook's
check-in steps, and the rest of its Refuse list, still bind.
"""


def duty_message() -> str:
    """The paste body. Pointers first, duty last, so bus text cannot outrank it."""
    return _DUTY


def _stop_requested() -> bool:
    return _STOP.is_file()


def root_lane_closed(root_id: str) -> tuple[bool, str]:
    """True only when the bus root is ``status=closed``. A transport miss is not closed."""
    from bus_watch.digest_budget import _bus, _get

    with _bus() as client:
        root = _get(client, f"/threads/{root_id}") or {}
    if not isinstance(root, dict) or root.get("_error"):
        return False, "lane_unread"
    status = str(root.get("status") or "").strip().lower()
    if status == "closed":
        return True, "lane_closed"
    return False, status or "open"


def _remote_cmd(message_path: str, repo: str, *, dry_run: bool) -> str:
    flags = "--dry-run" if dry_run else ""
    return (
        "export WAYLAND_DISPLAY=wayland-1 XDG_RUNTIME_DIR=/run/user/1000; "
        f"python3 {repo}/scripts/orchestrator_tab_keystroke.py glass-launch "
        f"--message-file {message_path} --repo {repo} {flags}"
    )


def keystroke(*, gui_host: str, dry_run: bool = False) -> dict[str, object]:
    """SSH a new-tab paste to the GUI host. Refuses a locked session on a real paste."""
    if not dry_run:
        from bus_watch.ide_hop import session_unreachable

        locked = session_unreachable(gui_host)
        if locked is not None:
            return {"ok": False, "phase": "session_locked", **locked}
    _MSG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path = _MSG_DIR / f"copilot-{_LANE}-{stamp}.md"
    path.write_text(duty_message(), encoding="utf-8")
    remote_msg = f"{_REPO}/{path.relative_to(_REPO)}"
    cmd = _remote_cmd(remote_msg, str(_REPO), dry_run=dry_run)
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
    result: dict[str, object] = {
        "ok": ok,
        "phase": "sent" if ok else "keystroke",
        "dry_run": dry_run,
        "returncode": proc.returncode,
        "stdout": proc.stdout[-800:],
        "stderr": proc.stderr[-800:],
        "message_path": str(path),
        "gui_host": gui_host,
        "lane": _LANE,
    }
    _record(result)
    return result


def _record(result: dict[str, object]) -> None:
    _STATE.parent.mkdir(parents=True, exist_ok=True)
    _STATE.write_text(
        json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **result}, indent=2)
        + "\n",
        encoding="utf-8",
    )


def loop(*, gui_host: str, interval_minutes: float) -> int:
    """Sleep, then paste. The sleep comes first so the arming tab is not duplicated."""
    while True:
        if _stop_requested():
            print(json.dumps({"ok": True, "stopped": True, "reason": "stop_file"}), flush=True)
            return 0
        closed, lane = root_lane_closed(_LANE)
        if closed:
            print(json.dumps({"ok": True, "stopped": True, "reason": lane}), flush=True)
            return 0
        time.sleep(max(interval_minutes, 0.1) * 60)
        if _stop_requested():
            print(json.dumps({"ok": True, "stopped": True, "reason": "stop_file"}), flush=True)
            return 0
        closed, lane = root_lane_closed(_LANE)
        if closed:
            print(json.dumps({"ok": True, "stopped": True, "reason": lane}), flush=True)
            return 0
        result = keystroke(gui_host=gui_host, dry_run=False)
        print(json.dumps(result), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cmd", choices=("once", "loop"))
    parser.add_argument("--gui-host", default=_DEFAULT_GUI)
    parser.add_argument("--interval-minutes", type=float, default=_INTERVAL_MINUTES)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.cmd == "loop":
        if args.dry_run:
            print(json.dumps({"ok": False, "phase": "dry_run_is_once"}))
            return 1
        return loop(gui_host=args.gui_host, interval_minutes=args.interval_minutes)
    result = keystroke(gui_host=args.gui_host, dry_run=args.dry_run)
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

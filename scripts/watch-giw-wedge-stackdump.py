#!/usr/bin/env python3
"""External GIW dual-probe watcher: wedge stackdump + death restart.

Stands OUTSIDE the GIW event loop. Two branches:

- Wedge: both ``/health`` and liveness fail while :8091 is still listening —
  SIGUSR1 dump (rate-limited) into ``stackdump.log``.
- Death: no live listener — page ``giw-down`` and ``manage start``, unless a
  restart window is open or the 3/30min restart budget is exhausted.

Safety: never signals a pid that has not logged ``SIGUSR1 stack dumps armed``
for that pid — unregistered SIGUSR1 would terminate the process.

Usage:
  scripts/watch-giw-wedge-tmux.sh
  scripts/watch-giw-wedge-stackdump.py --once   # single poll (smoke)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
from watch_giw_wedge_recover import (  # noqa: E402
    ActionBudget,
    restart_window_open,
    start_giw,
)

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8091
_HEALTH_PATH = "/health"
_LIVENESS_PATH = "/api/v1/git/cursor-auto/liveness"
_STACKDUMP_LOG = Path("/tmp/logs/git-integration-worker/stackdump.log")
_WORKER_LOG = Path("/tmp/logs/git-integration-worker/git-integration-worker.log")
_EMAIL_BRIDGE_SOCK = os.environ.get(
    "EMAIL_BRIDGE_SOCK", "/tmp/universal-protocol/email-bridge.sock"
)
_PID_RE = re.compile(r"pid=(\d+)")
_ARM_RE = re.compile(
    r"SIGUSR1 stack dumps armed:\s*pid=(?P<pid>\d+)\s+path=(?P<path>\S+)"
)


def _log(msg: str) -> None:
    print(f"[giw-wedge-watch] {msg}", flush=True)


def _probe(url: str, *, timeout_s: float) -> tuple[bool, str]:
    """Return (ok, detail). ok means HTTP 200 with a non-empty body."""
    try:
        with urlopen(Request(url, method="GET"), timeout=timeout_s) as resp:
            body = resp.read()
            code = getattr(resp, "status", None) or resp.getcode()
            if int(code) != 200:
                return False, f"http_{code}"
            if not body:
                return False, "empty_body"
            return True, f"http_200/{len(body)}b"
    except HTTPError as exc:
        return False, f"http_{exc.code}"
    except TimeoutError:
        return False, "timeout"
    except URLError as exc:
        reason = getattr(exc, "reason", exc)
        return False, f"url_error:{reason}"
    except OSError as exc:
        return False, f"os_error:{exc}"


def _listener_pid(port: int) -> int | None:
    proc = subprocess.run(
        ["ss", "-ltnp", f"( sport = :{port} )"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    matches = _PID_RE.findall(proc.stdout or "")
    if not matches:
        return None
    return int(matches[0])


def _recv_q(port: int) -> int | None:
    proc = subprocess.run(
        ["ss", "-ltn", f"( sport = :{port} )"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    for line in (proc.stdout or "").splitlines():
        if "LISTEN" not in line:
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1].isdigit():
            return int(parts[1])
    return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _arm_confirmed(pid: int, *, log_path: Path = _WORKER_LOG) -> bool:
    if not log_path.is_file():
        return False
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    for match in _ARM_RE.finditer(text):
        if int(match.group("pid")) == pid:
            return True
    return False


def _fire_usr1(pid: int, *, gap_s: float) -> None:
    os.kill(pid, signal.SIGUSR1)
    _log(f"SIGUSR1 #1 → pid={pid}")
    time.sleep(gap_s)
    if not _pid_alive(pid):
        _log(f"pid={pid} gone after first SIGUSR1 — aborting second")
        return
    os.kill(pid, signal.SIGUSR1)
    _log(f"SIGUSR1 #2 → pid={pid}")


def _page(*, subject: str, body: str, tag: str) -> bool:
    if os.environ.get("PAGER_NOTIFY_ENABLED", "1").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        _log("pager disabled (PAGER_NOTIFY_ENABLED=0)")
        return False
    payload = {"subject": subject[:120], "body": body[:4000], "tag": tag[:40]}
    proc = subprocess.run(
        [
            "curl",
            "-sS",
            "--unix-socket",
            _EMAIL_BRIDGE_SOCK,
            "-H",
            "Content-Type: application/json",
            "-d",
            json.dumps(payload),
            "http://localhost/pager/notify",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        _log(f"pager curl failed: {proc.stderr.strip()}")
        return False
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        _log(f"pager bad response: {proc.stdout!r}")
        return False
    ok = str(data.get("status")) == "sent"
    _log(f"pager: {data}")
    return ok


def _base_url(host: str, port: int) -> str:
    return f"http://{host}:{port}"


def _poll_once(
    *,
    host: str,
    port: int,
    timeout_s: float,
) -> dict[str, Any]:
    base = _base_url(host, port)
    health_ok, health_detail = _probe(f"{base}{_HEALTH_PATH}", timeout_s=timeout_s)
    live_ok, live_detail = _probe(f"{base}{_LIVENESS_PATH}", timeout_s=timeout_s)
    pid = _listener_pid(port)
    recv_q = _recv_q(port)
    alive = bool(pid and _pid_alive(pid))
    armed = bool(pid and _arm_confirmed(pid))
    dual_dead = (not health_ok) and (not live_ok)
    wedge = dual_dead and alive
    return {
        "health_ok": health_ok,
        "health_detail": health_detail,
        "live_ok": live_ok,
        "live_detail": live_detail,
        "pid": pid,
        "pid_alive": alive,
        "armed": armed,
        "recv_q": recv_q,
        "dual_dead": dual_dead,
        "wedge": wedge,
    }


def _handle_wedge(
    snap: dict[str, Any],
    *,
    args: argparse.Namespace,
    last_fire_mono: float,
    dump_budget: ActionBudget,
) -> float:
    pid = snap["pid"]
    _log(
        f"WEDGE pid={pid} armed={snap['armed']} recv_q={snap['recv_q']} "
        f"health={snap['health_detail']} live={snap['live_detail']}"
    )
    now = time.monotonic()
    if now - last_fire_mono < args.cooldown_s:
        _log(
            f"cooldown — skip fire "
            f"({args.cooldown_s - (now - last_fire_mono):.0f}s left)"
        )
        return last_fire_mono
    if not dump_budget.allow(now):
        _log("dump budget exhausted — skip SIGUSR1 (3 / 30 min)")
        return last_fire_mono
    if not snap["armed"] and not args.force_unarmed:
        _log(
            f"refusing SIGUSR1 — no arm log for pid={pid} "
            f"(would kill unregistered handler); pass --force-unarmed to override"
        )
        return last_fire_mono
    assert isinstance(pid, int)
    before = _STACKDUMP_LOG.stat().st_size if _STACKDUMP_LOG.is_file() else 0
    _fire_usr1(pid, gap_s=args.usr1_gap_s)
    dump_budget.record(now)
    time.sleep(0.5)
    after = _STACKDUMP_LOG.stat().st_size if _STACKDUMP_LOG.is_file() else 0
    grew = after > before
    _log(
        f"capture fired pid={pid} stackdump_grew={grew} "
        f"size={before}->{after} path={_STACKDUMP_LOG}"
    )
    if not args.no_page:
        _page(
            subject="ULG GIW wedge — stackdump fired",
            body=(
                f"pid={pid} recv_q={snap['recv_q']} "
                f"health={snap['health_detail']} "
                f"live={snap['live_detail']} "
                f"stackdump={_STACKDUMP_LOG} grew={grew}"
            ),
            tag="giw-wedge",
        )
    return time.monotonic()


def _handle_death(
    snap: dict[str, Any],
    *,
    args: argparse.Namespace,
    restart_budget: ActionBudget,
) -> None:
    _log(
        f"DEAD pid={snap['pid']} health={snap['health_detail']} "
        f"live={snap['live_detail']}"
    )
    if restart_window_open():
        _log("restart window open — suppress auto-restart")
        return
    now = time.monotonic()
    if not restart_budget.allow(now):
        _log("restart budget exhausted — skip manage start (3 / 30 min)")
        return
    if not args.no_page:
        _page(
            subject="ULG GIW down — restarting",
            body=(
                f"pid={snap['pid']} health={snap['health_detail']} "
                f"live={snap['live_detail']} — manage start git_integration_worker"
            ),
            tag="giw-down",
        )
    result = start_giw()
    restart_budget.record(now)
    _log(f"manage start git_integration_worker → {result}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=_DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT)
    parser.add_argument("--poll-s", type=float, default=10.0)
    parser.add_argument("--timeout-s", type=float, default=2.0)
    parser.add_argument("--usr1-gap-s", type=float, default=5.0)
    parser.add_argument(
        "--cooldown-s",
        type=float,
        default=300.0,
        help="min seconds between capture fires (default 300)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="single poll then exit (0=healthy, 2=wedge-not-fired, 3=fired)",
    )
    parser.add_argument(
        "--no-page",
        action="store_true",
        help="do not SMS after a capture fire",
    )
    parser.add_argument(
        "--force-unarmed",
        action="store_true",
        help="danger: fire even when arm log line is missing for pid",
    )
    args = parser.parse_args()

    last_fire_mono = 0.0
    dump_budget = ActionBudget()
    restart_budget = ActionBudget()
    _log(
        f"start host={args.host}:{args.port} poll={args.poll_s}s "
        f"timeout={args.timeout_s}s cooldown={args.cooldown_s}s "
        f"stackdump={_STACKDUMP_LOG}"
    )

    while True:
        snap = _poll_once(host=args.host, port=args.port, timeout_s=args.timeout_s)
        pid = snap["pid"]
        if snap["health_ok"] and snap["live_ok"]:
            _log(
                f"ok pid={pid} armed={snap['armed']} recv_q={snap['recv_q']} "
                f"health={snap['health_detail']} live={snap['live_detail']}"
            )
        elif snap["wedge"]:
            fired_before = dump_budget.remaining()
            last_fire_mono = _handle_wedge(
                snap,
                args=args,
                last_fire_mono=last_fire_mono,
                dump_budget=dump_budget,
            )
            if args.once:
                return 3 if dump_budget.remaining() < fired_before else 2
        elif not snap["pid_alive"]:
            _handle_death(snap, args=args, restart_budget=restart_budget)
            if args.once:
                return 4
        else:
            _log(
                f"partial pid={pid} alive={snap['pid_alive']} "
                f"health={snap['health_detail']} live={snap['live_detail']}"
            )

        if args.once:
            return 2 if snap["wedge"] else 0
        time.sleep(args.poll_s)


if __name__ == "__main__":
    raise SystemExit(main())

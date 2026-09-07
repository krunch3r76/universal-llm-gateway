#!/usr/bin/env python3
"""Cheap backup + steer watcher for CDP hop reactor.

Polls the detached ``cdp-hop`` supervisor + reactor log.

**Mechanical:** respawn reactor when the process dies (bounded).

**Steer:** on new stall episodes, fire one bounded ``cursor-auto`` confer judgment,
write ``tmp/watch/cdp-hop-reactor/steer-latest.md``, post to the Fable bus thread,
emit ``stall-pop:`` + ``steer-wake:`` for the attended IDE seat.

Arm:
  scripts/watch-supervise.sh start --label cdp-backup -- \\
    scripts/watch-cdp-hop-backup.py --fable-thread 10188

IDE wake: notify_on_output on ``stall-pop:|steer-wake:`` while tailing:
  scripts/watch-supervise.sh tail --label cdp-backup
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bus_watch.stall_pop import emit_stall_pop, should_emit_stall_pop
from bus_watch.state import write_state
from cdp_hop_watch_steer import (
    bus_token,
    confer_steer_judgment,
    gather_steer_context,
    post_steer_turn,
    steer_path,
    write_steer_hint,
)

_REPO = Path(__file__).resolve().parents[1]
_WATCH_DIR = Path(os.environ.get("WATCH_DIR", str(_REPO / "tmp/watchers")))
_STATE_DIR = Path(os.environ.get("CDP_HOP_STATE_DIR", str(_REPO / "tmp/watch/cdp-hop-reactor")))
_MCP_YAML = Path.home() / ".gateway" / "mcp.yaml"
_AGENT_BUS_SOCK = os.environ.get("AGENT_BUS_SOCK", "/tmp/universal-protocol/agent-bus.sock")
_STARGATE = os.environ.get("CDP_BACKUP_STARGATE", "http://localhost:9999")
_CDP_ASK = os.environ.get("CDP_BACKUP_CDP_ASK", "http://jupiter:8770")

POLL_S = int(os.environ.get("CDP_BACKUP_POLL_S", "120"))
STALL_S = int(os.environ.get("CDP_BACKUP_STALL_S", "900"))
MAX_RESPAWNS = int(os.environ.get("CDP_BACKUP_MAX_RESPAWNS", "2"))
MAX_STEERS = int(os.environ.get("CDP_BACKUP_MAX_STEERS", "4"))
MAX_HOURS = float(os.environ.get("CDP_BACKUP_MAX_HOURS", "6"))
_STEER_WAKE_PREFIX = "steer-wake:"


def log(msg: str, **fields: Any) -> None:
    rec = {"ts": datetime.now(UTC).isoformat(timespec="seconds"), "msg": msg}
    rec.update({k: v for k, v in fields.items() if v is not None})
    print(json.dumps(rec), flush=True)


def emit_steer_wake(reason: str) -> None:
    print(f"{_STEER_WAKE_PREFIX} {reason.strip()}", flush=True)


def pid_alive(pid: int | None) -> bool:
    return bool(pid) and os.path.exists(f"/proc/{pid}")


def read_pid(label: str) -> int | None:
    path = _WATCH_DIR / f"{label}.pid"
    if not path.is_file():
        return None
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def log_tail_events(log_path: Path, *, since_pos: int) -> tuple[list[dict[str, Any]], int]:
    if not log_path.is_file():
        return [], since_pos
    text = log_path.read_text(encoding="utf-8", errors="replace")
    new_text = text[since_pos:]
    pos = len(text)
    events: list[dict[str, Any]] = []
    for line in new_text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events, pos


def respawn_reactor(fable_thread: str, deadline_hours: float) -> bool:
    script = _REPO / "scripts" / "cdp-hop-reactor-night.py"
    supervise = _REPO / "scripts" / "watch-supervise.sh"
    cmd = [
        str(supervise),
        "start",
        "--label",
        "cdp-hop",
        "--",
        str(script),
        "--fable-thread",
        fable_thread,
        "--deadline-hours",
        str(deadline_hours),
    ]
    proc = subprocess.run(cmd, cwd=_REPO, capture_output=True, text=True, check=False)
    ok = proc.returncode == 0
    log("respawn_reactor", ok=ok, stdout=proc.stdout.strip()[:300], stderr=proc.stderr.strip()[:300])
    return ok


def maybe_steer(
    *,
    fable_thread: str,
    reason: str,
    steer_count: int,
    steered_reasons: set[str],
    reactor_log: Path,
    enable_steer: bool,
) -> tuple[int, set[str]]:
    if not enable_steer or steer_count >= MAX_STEERS or reason in steered_reasons:
        return steer_count, steered_reasons

    context = gather_steer_context(
        fable_thread=fable_thread,
        reason=reason,
        reactor_state_path=_STATE_DIR / "state.json",
        reactor_log_path=reactor_log,
        cdp_ask_base=_CDP_ASK,
    )
    judgment = confer_steer_judgment(
        stargate_base=_STARGATE,
        fable_thread=fable_thread,
        reason=reason,
        context=context,
    )
    if not judgment:
        log("steer_confer_failed", reason=reason)
        return steer_count, steered_reasons

    write_steer_hint(steer_path(_STATE_DIR), reason=reason, judgment=judgment)
    try:
        posted = post_steer_turn(
            sock=_AGENT_BUS_SOCK,
            token=bus_token(_MCP_YAML),
            thread=fable_thread,
            reason=reason,
            judgment=judgment,
        )
    except RuntimeError as exc:
        log("steer_bus_skip", error=str(exc)[:200])
        posted = False

    steer_count += 1
    steered_reasons.add(reason)
    emit_steer_wake(reason)
    log("steer_complete", reason=reason, steer_count=steer_count, bus_posted=posted)
    return steer_count, steered_reasons


def handle_stall(
    last_stall_reason: str | None,
    reason: str,
    *,
    fable_thread: str,
    steer_count: int,
    steered_reasons: set[str],
    reactor_log: Path,
    enable_steer: bool,
) -> tuple[str | None, int, set[str]]:
    emit, next_reason = should_emit_stall_pop(
        last_reason=last_stall_reason,
        reason=reason,
        stall_active=True,
    )
    if emit:
        emit_stall_pop(reason)
        steer_count, steered_reasons = maybe_steer(
            fable_thread=fable_thread,
            reason=reason,
            steer_count=steer_count,
            steered_reasons=steered_reasons,
            reactor_log=reactor_log,
            enable_steer=enable_steer,
        )
        return next_reason, steer_count, steered_reasons
    return last_stall_reason, steer_count, steered_reasons


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fable-thread", required=True)
    parser.add_argument("--reactor-label", default="cdp-hop")
    parser.add_argument("--deadline-hours", type=float, default=6.0)
    parser.add_argument(
        "--no-steer",
        action="store_true",
        help="disable autonomous confer steering (backup-only)",
    )
    parser.add_argument(
        "--state-file",
        default="",
        help="watch-supervise durable status path",
    )
    args = parser.parse_args()

    state_path = Path(args.state_file) if str(args.state_file).strip() else None
    reactor_log = _WATCH_DIR / f"{args.reactor_label}.log"
    log_pos = reactor_log.stat().st_size if reactor_log.is_file() else 0
    last_log_activity = time.monotonic()
    last_stall_reason: str | None = None
    respawn_count = 0
    steer_count = 0
    steered_reasons: set[str] = set()
    started = time.monotonic()
    enable_steer = not args.no_steer

    log(
        "backup_armed",
        fable_thread=args.fable_thread,
        reactor_label=args.reactor_label,
        poll_s=POLL_S,
        stall_s=STALL_S,
        steer=enable_steer,
        max_steers=MAX_STEERS,
    )
    if state_path is not None:
        write_state(
            state_path,
            status="armed",
            fable_thread=args.fable_thread,
            reactor_label=args.reactor_label,
            label="cdp-backup",
            steer=enable_steer,
        )

    while True:
        elapsed_h = (time.monotonic() - started) / 3600.0
        if MAX_HOURS > 0 and elapsed_h >= MAX_HOURS:
            log("backup_expired", elapsed_h=round(elapsed_h, 2))
            if state_path is not None:
                write_state(state_path, status="expired")
            return 0

        reactor_pid = read_pid(args.reactor_label)
        reactor_alive = pid_alive(reactor_pid)

        events, log_pos = log_tail_events(reactor_log, since_pos=log_pos)
        if events:
            last_log_activity = time.monotonic()
            for ev in events:
                event = str(ev.get("event") or "")
                if event == "stop":
                    last_stall_reason, steer_count, steered_reasons = handle_stall(
                        last_stall_reason,
                        f"reactor_stop:{ev.get('reason', 'unknown')}",
                        fable_thread=args.fable_thread,
                        steer_count=steer_count,
                        steered_reasons=steered_reasons,
                        reactor_log=reactor_log,
                        enable_steer=enable_steer,
                    )
                elif event in {
                    "dispatch_fail",
                    "fable_attach_timeout",
                    "harvest_failed",
                    "fable_gate_409",
                    "harvest_miss_breaker",
                    "harvest_unreachable",
                }:
                    stall = {
                        "fable_gate_409": "reactor_gate_409",
                        "harvest_miss_breaker": "reactor_harvest_unreachable",
                        "harvest_unreachable": "reactor_harvest_unreachable",
                    }.get(event, f"reactor_{event}")
                    last_stall_reason, steer_count, steered_reasons = handle_stall(
                        last_stall_reason,
                        stall,
                        fable_thread=args.fable_thread,
                        steer_count=steer_count,
                        steered_reasons=steered_reasons,
                        reactor_log=reactor_log,
                        enable_steer=enable_steer,
                    )

        if not reactor_alive:
            if respawn_count < MAX_RESPAWNS:
                if respawn_reactor(args.fable_thread, args.deadline_hours):
                    respawn_count += 1
                    last_log_activity = time.monotonic()
                    last_stall_reason, steer_count, steered_reasons = handle_stall(
                        last_stall_reason,
                        f"reactor_respawned:{respawn_count}",
                        fable_thread=args.fable_thread,
                        steer_count=steer_count,
                        steered_reasons=steered_reasons,
                        reactor_log=reactor_log,
                        enable_steer=enable_steer,
                    )
                else:
                    last_stall_reason, steer_count, steered_reasons = handle_stall(
                        last_stall_reason,
                        "reactor_respawn_failed",
                        fable_thread=args.fable_thread,
                        steer_count=steer_count,
                        steered_reasons=steered_reasons,
                        reactor_log=reactor_log,
                        enable_steer=enable_steer,
                    )
            else:
                last_stall_reason, steer_count, steered_reasons = handle_stall(
                    last_stall_reason,
                    "reactor_dead_respawn_budget",
                    fable_thread=args.fable_thread,
                    steer_count=steer_count,
                    steered_reasons=steered_reasons,
                    reactor_log=reactor_log,
                    enable_steer=enable_steer,
                )
        else:
            silent_s = time.monotonic() - last_log_activity
            if silent_s >= STALL_S:
                last_stall_reason, steer_count, steered_reasons = handle_stall(
                    last_stall_reason,
                    f"reactor_log_silent:{int(silent_s)}s",
                    fable_thread=args.fable_thread,
                    steer_count=steer_count,
                    steered_reasons=steered_reasons,
                    reactor_log=reactor_log,
                    enable_steer=enable_steer,
                )
            else:
                _, last_stall_reason = should_emit_stall_pop(
                    last_reason=last_stall_reason,
                    reason="",
                    stall_active=False,
                )

        if state_path is not None:
            write_state(
                state_path,
                status="polling",
                reactor_alive=reactor_alive,
                reactor_pid=reactor_pid,
                respawn_count=respawn_count,
                steer_count=steer_count,
                silent_s=round(time.monotonic() - last_log_activity),
            )

        log(
            "heartbeat",
            reactor_alive=reactor_alive,
            reactor_pid=reactor_pid,
            respawn_count=respawn_count,
            steer_count=steer_count,
            silent_s=int(time.monotonic() - last_log_activity),
        )
        time.sleep(POLL_S)


if __name__ == "__main__":
    sys.exit(main())

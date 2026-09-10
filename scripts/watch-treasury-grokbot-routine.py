#!/usr/bin/env python3
"""Interval keystroke launcher for Grok Bot treasury routine (replaces app Routine timer).

Arm:
  scripts/watch-supervise.sh start --label treasury-grokbot-routine -- \\
    $HOME/.venvs/universal/bin/python scripts/watch-treasury-grokbot-routine.py

Default interval 600s (10m). Override: GROKBOT_ROUTINE_INTERVAL_S=840
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from bus_watch.state import paths_for, write_state

_REPO = Path(__file__).resolve().parents[1]
_PYTHON = Path(os.environ.get("UNIVERSAL_PYTHON", Path.home() / ".venvs/universal/bin/python"))
_LAUNCH = _REPO / "scripts/grokbot-routine-launch.py"
_DEFAULT_INTERVAL_S = int(os.environ.get("GROKBOT_ROUTINE_INTERVAL_S", "600"))


def _launch(dry_run: bool = False) -> dict:
    cmd = [_PYTHON, str(_LAUNCH), "launch"]
    if dry_run:
        cmd.append("--dry-run")
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=_REPO, check=False)
    try:
        return json.loads(proc.stdout.strip() or "{}")
    except json.JSONDecodeError:
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="treasury-grokbot-routine")
    parser.add_argument("--state-file", default="")
    parser.add_argument("--interval-s", type=int, default=_DEFAULT_INTERVAL_S)
    parser.add_argument("--once", action="store_true", help="Single launch then exit")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    paths = paths_for(args.label)
    state_path = Path(args.state_file) if str(args.state_file).strip() else paths.state_file
    paths.log_file.parent.mkdir(parents=True, exist_ok=True)

    def log(line: str) -> None:
        # watch-supervise redirects stdout → log; do not append here (duplicate lines).
        print(line, flush=True)

    started = time.time()
    cycles = 0
    while True:
        cycles += 1
        log(f"… cycle={cycles} launching grokbot routine keystroke")
        result = _launch(dry_run=args.dry_run)
        write_state(
            state_path,
            status="polling",
            label=args.label,
            cycles=cycles,
            last_launch=result,
            interval_s=args.interval_s,
            elapsed_s=int(time.time() - started),
        )
        if not result.get("ok"):
            log(f"… launch_failed {json.dumps(result, default=str)[:400]}")
        else:
            log(f"… launch_ok holder={result.get('holder')}")

        if args.once:
            break
        log(f"… sleep interval_s={args.interval_s}")
        time.sleep(args.interval_s)

    write_state(
        state_path,
        status="complete",
        label=args.label,
        cycles=cycles,
        elapsed_s=int(time.time() - started),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

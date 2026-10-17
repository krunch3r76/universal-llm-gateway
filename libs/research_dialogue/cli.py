from __future__ import annotations

import argparse
import sys
from pathlib import Path

from research_dialogue.session import run_close, run_smoke, run_start, run_turn


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="research_dialogue")
    sub = parser.add_subparsers(dest="cmd", required=True)

    smoke = sub.add_parser("smoke")
    smoke.add_argument("--session", required=True, type=Path)

    start = sub.add_parser("start")
    start.add_argument("--session", required=True, type=Path)
    start.add_argument("--deliverable", required=True, type=Path)
    start.add_argument("--minor-notes", required=True, type=Path)

    turn = sub.add_parser("turn")
    turn.add_argument("--session", required=True, type=Path)
    turn.add_argument("--challenge-file", type=Path, default=None)

    close = sub.add_parser("close")
    close.add_argument("--session", required=True, type=Path)
    close.add_argument("--stop-reason", required=True)
    close.add_argument("--no-p4", action="store_true")

    args = parser.parse_args(argv)
    if args.cmd == "smoke":
        return run_smoke(args.session)
    if args.cmd == "start":
        run_start(args.session, deliverable=args.deliverable, minor_notes=args.minor_notes)
        return 0
    if args.cmd == "turn":
        code, _ = run_turn(args.session, challenge_file=args.challenge_file)
        return code
    if args.cmd == "close":
        run_close(args.session, stop_reason=args.stop_reason, no_p4=args.no_p4)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from research_dialogue.driver import preflight_guard, run_driver
from research_dialogue.session import run_close, run_smoke, run_start, run_turn


def _add_pid_args(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--expect-stargate-pid", required=True)
    sp.add_argument("--expect-cloud-proxy-pid", required=True)
    sp.add_argument(
        "--stream",
        action="store_true",
        help="Use SSE streaming (S1 deviation; floors at gate until FLOOR-FIX F1).",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="research_dialogue")
    sub = parser.add_subparsers(dest="cmd", required=True)

    smoke = sub.add_parser("smoke")
    smoke.add_argument("--session", required=True, type=Path)
    _add_pid_args(smoke)

    start = sub.add_parser("start")
    start.add_argument("--session", required=True, type=Path)
    start.add_argument("--deliverable", required=True, type=Path)
    start.add_argument("--minor-notes", required=True, type=Path)

    turn = sub.add_parser("turn")
    turn.add_argument("--session", required=True, type=Path)
    turn.add_argument("--challenge-file", type=Path, default=None)
    _add_pid_args(turn)

    close = sub.add_parser("close")
    close.add_argument("--session", required=True, type=Path)
    close.add_argument("--stop-reason", required=True)
    close.add_argument("--no-p4", action="store_true")
    _add_pid_args(close)

    args = parser.parse_args(argv)
    if args.cmd == "smoke":
        code, rec = preflight_guard(
            expect_stargate_pid=args.expect_stargate_pid,
            expect_cloud_proxy_pid=args.expect_cloud_proxy_pid,
            read_fn=__import__("research_dialogue.ledger_read", fromlist=["read_status"]).read_status,
        )
        if code != 0:
            print(json.dumps(rec, default=str))
            return code
        return run_smoke(args.session, stream=bool(args.stream))
    if args.cmd == "start":
        run_start(args.session, deliverable=args.deliverable, minor_notes=args.minor_notes)
        return 0
    if args.cmd == "turn":
        code, summary = run_driver(
            "turn",
            args.session,
            expect_stargate_pid=args.expect_stargate_pid,
            expect_cloud_proxy_pid=args.expect_cloud_proxy_pid,
            challenge_file=args.challenge_file,
            stream=bool(args.stream),
        )
        print(json.dumps(summary, default=str))
        return code
    if args.cmd == "close":
        code, rec = preflight_guard(
            expect_stargate_pid=args.expect_stargate_pid,
            expect_cloud_proxy_pid=args.expect_cloud_proxy_pid,
            read_fn=__import__("research_dialogue.ledger_read", fromlist=["read_status"]).read_status,
        )
        if code != 0:
            print(json.dumps(rec, default=str))
            return code
        if args.no_p4:
            code, line = run_close(args.session, stop_reason=args.stop_reason, no_p4=True)
            print(json.dumps(line))
            return code
        code, summary = run_driver(
            "close",
            args.session,
            expect_stargate_pid=args.expect_stargate_pid,
            expect_cloud_proxy_pid=args.expect_cloud_proxy_pid,
            stop_reason=args.stop_reason,
            stream=bool(args.stream),
        )
        print(json.dumps(summary, default=str))
        return code
    return 1


if __name__ == "__main__":
    sys.exit(main())

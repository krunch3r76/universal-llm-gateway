"""``python -m scripts.model_manager.ui.dispatch_monitor`` — live or fixture ``--watch``."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from scripts.model_manager.ui.dispatch_monitor.core import __main__ as core_main
from scripts.model_manager.ui.dispatch_monitor.ulg.controller import MonitorController
from scripts.model_manager.ui.dispatch_monitor.ulg.manage_charter_hold import (
    charter_hold_status,
    charter_pause,
    charter_resume,
)
from scripts.model_manager.ui.dispatch_monitor.ulg.manage_reload import charter_reload
from scripts.model_manager.ui.dispatch_monitor.ulg.reconcile_on_click import (
    ReconcileOnClick,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dispatch_monitor",
        description="Dispatch supervisor monitor — live Event Service or fixture replay.",
    )
    parser.add_argument(
        "--watch",
        metavar="TARGET",
        required=False,
        help="'live' for Event Service subscribe, or a JSONL fixture path",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="text sink or canonical JSON projection frames (live mode)",
    )
    parser.add_argument(
        "--seed-minutes",
        type=int,
        default=60,
        help="cold-start history window for live mode (default 60)",
    )
    parser.add_argument(
        "--charter-reload",
        action="store_true",
        help="fire manage.sock charter_reload then exit",
    )
    parser.add_argument(
        "--charter-pause",
        metavar="REASON",
        nargs="?",
        const="operator",
        default=None,
        help="fire manage.sock charter_pause [REASON] then exit",
    )
    parser.add_argument(
        "--charter-resume",
        action="store_true",
        help="fire manage.sock charter_resume then exit",
    )
    parser.add_argument(
        "--charter-hold-status",
        action="store_true",
        help="fire manage.sock charter_hold_status then exit",
    )
    parser.add_argument(
        "--reconcile",
        metavar="SUBJECT",
        default=None,
        help="reconcile one subject (dispatch_id|root_id|request_id) then exit",
    )
    parser.add_argument(
        "--frames",
        choices=("final", "each"),
        default="final",
        help="fixture replay only — passed through to core",
    )
    parser.add_argument(
        "--now-ms",
        type=int,
        default=None,
        help="fixture replay only — freeze clock",
    )
    parser.add_argument(
        "--suppress-unchanged",
        action="store_true",
        help="fixture replay only",
    )
    return parser


def _run_fixture(argv: Sequence[str]) -> int:
    return core_main.main(argv)


def _run_live(args: argparse.Namespace) -> int:
    controller = MonitorController(seed_minutes=args.seed_minutes)

    def sink(line: str) -> None:
        print(line, flush=True)

    try:
        asyncio.run(
            controller.run(
                on_frame=sink,
                json_frames=args.format == "json",
                command_endpoint="manage.sock:charter_reload",
            )
        )
    except KeyboardInterrupt:
        return 0
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.charter_reload:
        result = charter_reload()
        print(result, flush=True)
        return 1 if "error" in result else 0

    if args.charter_pause is not None:
        result = charter_pause(reason=args.charter_pause)
        print(result, flush=True)
        return 1 if "error" in result else 0

    if args.charter_resume:
        result = charter_resume()
        print(result, flush=True)
        return 1 if "error" in result else 0

    if args.charter_hold_status:
        result = charter_hold_status()
        print(result, flush=True)
        return 1 if "error" in result else 0

    if args.reconcile:
        controller = MonitorController(reconcile=ReconcileOnClick())
        result = controller.trigger_reconcile(args.reconcile)
        print(result, flush=True)
        return 1 if result.get("error") else 0

    if not args.watch:
        parser.error("--watch is required unless using a --charter-* one-shot")

    if args.watch == "live":
        return _run_live(args)

    fixture_argv = ["--watch", args.watch]
    if args.frames:
        fixture_argv.extend(["--frames", args.frames])
    if args.format:
        fixture_argv.extend(["--format", args.format])
    if args.now_ms is not None:
        fixture_argv.extend(["--now-ms", str(args.now_ms)])
    if args.suppress_unchanged:
        fixture_argv.append("--suppress-unchanged")
    return _run_fixture(fixture_argv)


if __name__ == "__main__":
    raise SystemExit(main())

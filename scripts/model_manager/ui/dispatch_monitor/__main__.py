"""``python -m scripts.model_manager.ui.dispatch_monitor`` — live or fixture ``--watch``."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from typing import Any

from scripts.model_manager.ui.dispatch_monitor.core import __main__ as core_main
from scripts.model_manager.ui.dispatch_monitor.core.curses_board import CursesBoard
from scripts.model_manager.ui.dispatch_monitor.core.dtos import (
    SupervisorProjection,
    Thresholds,
)
from scripts.model_manager.ui.dispatch_monitor.core.model import Model
from scripts.model_manager.ui.dispatch_monitor.core.replay import JsonlEventSource
from scripts.model_manager.ui.dispatch_monitor.ulg.controller import MonitorController
from scripts.model_manager.ui.dispatch_monitor.ulg.manage_charter_hold import (
    charter_hold_status,
    charter_pause,
    charter_resume,
)
from scripts.model_manager.ui.dispatch_monitor.ulg.manage_reload import charter_reload
from scripts.model_manager.ui.dispatch_monitor.ulg.projection_hub import BroadcastHub
from scripts.model_manager.ui.dispatch_monitor.ulg.reconcile_on_click import (
    ReconcileOnClick,
)


def run_fixture_board(
    stdscr: Any,
    fixture_path: str,
    *,
    now_ms: int | None = None,
    seed_minutes: int = 60,
) -> SupervisorProjection:
    """Drive :class:`CursesBoard` from a JSONL fixture via in-process ``BroadcastHub``.

    Same shape as live ``--watch`` (hub publish → view sink), without Event Service,
    UDS, or an external compositor. Intended for fixture replay and headless tests.
    """
    source = JsonlEventSource.from_path(fixture_path)
    model = Model(Thresholds())
    hub = BroadcastHub()
    board = CursesBoard(stdscr, seed_minutes=seed_minutes)
    board.set_status("fixture")
    painted: list[SupervisorProjection] = []

    def _sink(frame: SupervisorProjection) -> None:
        board.paint(frame)
        painted.append(frame)

    hub.subscribe(_sink)
    source.subscribe(model.apply)
    clock = source.max_ts() if now_ms is None else now_ms
    hub.publish(model.derive(clock))
    if not painted:
        raise RuntimeError("fixture board hub published but no sink ran")
    return painted[-1]


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
        choices=("text", "json", "board"),
        default="text",
        help="text sink, JSON frames, or curses board (fixture replay uses board in-process)",
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


def _run_fixture_board(args: argparse.Namespace) -> int:
    import curses

    def _main(stdscr: Any) -> None:
        run_fixture_board(
            stdscr,
            args.watch,
            now_ms=args.now_ms,
            seed_minutes=args.seed_minutes,
        )
        # Hold one paint so an interactive operator can read the frame; tests call
        # run_fixture_board directly with a fake screen and never enter wrapper.
        try:
            stdscr.getch()
        except curses.error:
            pass

    curses.wrapper(_main)
    return 0


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
        if args.format == "board":
            parser.error("--format board is fixture-only; use scripts/watch-dispatch-board for live")
        return _run_live(args)

    if args.format == "board":
        return _run_fixture_board(args)

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

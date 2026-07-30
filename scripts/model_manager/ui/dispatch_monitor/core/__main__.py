"""``python -m dispatch_monitor_core`` -- the ``--watch`` debug entry.

Pre-graft, this replays a JSONL fixture through the Model and prints frames. It is
a **degenerate Controller**: it owns the only I/O in the core (reading a fixture,
writing stdout), does the clock, and derives-then-publishes exactly as the real
Controller will::

    now  = clock.now_ms()
    proj = model.derive(now)          # pure, no I/O
    if proj.fingerprint != last:
        hub.publish(proj)             # here: print

Post-graft the shape is unchanged; ``JsonlEventSource`` becomes ``UlgEventSource``
and ``print`` becomes ``libs/projection``'s ``BroadcastHub``. Keeping the debug
harness the same shape as the production loop is what makes the ``--watch`` gate
meaningful: it exercises the real derivation path rather than a side-channel.

Usage::

    python -m dispatch_monitor_core --watch fixtures/charter-admit-run-terminal.jsonl
    python -m dispatch_monitor_core --watch f.jsonl --frames each --format json
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from .codec import ProjectionCodec
from .dtos import Thresholds
from .model import Model
from .replay import JsonlEventSource


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the watch entry."""
    parser = argparse.ArgumentParser(
        prog="dispatch_monitor_core",
        description="Replay dispatch signals through the Model and render frames.",
    )
    parser.add_argument(
        "--watch",
        metavar="FIXTURE",
        required=True,
        help="JSONL fixture to replay ('-' reads stdin)",
    )
    parser.add_argument(
        "--frames",
        choices=("final", "each"),
        default="final",
        help="render only the last frame, or one frame per folded record",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="text sink, or canonical JSON projection frames",
    )
    parser.add_argument(
        "--now-ms",
        type=int,
        default=None,
        help="freeze the clock at this timestamp (default: fixture high-water)",
    )
    parser.add_argument(
        "--command-endpoint",
        default=None,
        help="endpoint hint to advertise in the handshake frame",
    )
    parser.add_argument(
        "--suppress-unchanged",
        action="store_true",
        help="with --frames each, skip frames whose fingerprint did not change",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the watch harness. Returns a process exit code."""
    args = build_parser().parse_args(argv)
    if args.watch == "-":
        from .replay import parse_lines

        source = JsonlEventSource(parse_lines(sys.stdin))
    else:
        source = JsonlEventSource.from_path(args.watch)
    now_ms = args.now_ms if args.now_ms is not None else source.max_ts()
    model = Model(Thresholds())

    if args.format == "json":
        print(ProjectionCodec.encode_handshake(args.command_endpoint))

    def emit(previous_fp: str | None) -> str:
        """Derive, render if changed, and return the new fingerprint."""
        frame = model.derive(now_ms)
        if args.suppress_unchanged and frame.fingerprint == previous_fp:
            return frame.fingerprint
        if args.format == "json":
            print(ProjectionCodec.encode_snapshot(frame))
        else:
            from .watch import render

            print(render(frame))
        return frame.fingerprint

    fingerprint: str | None = None
    if args.frames == "each":
        for record in source.records:
            model.apply(record)
            fingerprint = emit(fingerprint)
    else:
        source.subscribe(model.apply)
        emit(None)
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry
    raise SystemExit(main())

"""CLI commands that expose the surface catalog and estates extract.

Kept out of ``cli.py`` so that file stays under the modularization line.
Callers: ``cli.main`` registers these as subcommands.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from unclaimed_property_hunter.estates_pipeline import run_estates
from unclaimed_property_hunter.record import latest_run_for_kinds
from unclaimed_property_hunter.result_surface import (
    format_operator_stderr,
    public_run_dict,
    surface_report_row,
)
from unclaimed_property_hunter.surfaces import SURFACES, catalog_dicts


def cmd_surfaces(_args: argparse.Namespace) -> int:
    """Print the frozen surface catalog as JSON without contacting the network."""
    print(json.dumps({"surfaces": catalog_dicts()}, indent=2))
    return 0


def cmd_estates(args: argparse.Namespace) -> int:
    """Run a completed estates-xlsx search and emit the public run dict."""
    record = run_estates(
        surname=args.surname,
        xlsx_path=Path(args.xlsx_path),
        download=args.download,
        also=list(args.also or []),
        substring=list(args.substring or []),
    )
    payload = public_run_dict(record)
    if record.corpus_fingerprint:
        payload["rows_scanned"] = record.corpus_fingerprint.rows_scanned
    print(json.dumps(payload, indent=2))
    stderr_line = format_operator_stderr(record)
    if stderr_line:
        print(stderr_line, file=sys.stderr)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Per-surface four-token verdict from last persisted run, or NOT EXECUTED."""
    rows = [
        surface_report_row(surface, latest_run_for_kinds(args.surname, surface.run_kinds))
        for surface in SURFACES
    ]
    print(json.dumps({"surname": args.surname, "surfaces": rows}, indent=2))
    return 0


def register_surface_commands(sub: argparse._SubParsersAction) -> None:
    """Register the ``surfaces``, ``estates``, and ``report`` hunter subcommands."""
    surfaces = sub.add_parser("surfaces")
    surfaces.set_defaults(func=cmd_surfaces)
    estates = sub.add_parser("estates")
    estates.add_argument("--surname", required=True)
    estates.add_argument("--also", action="append", default=[])
    estates.add_argument("--substring", action="append", default=[])
    estates.add_argument("--xlsx-path", required=True)
    estates.add_argument("--download", action="store_true")
    estates.set_defaults(func=cmd_estates)
    report = sub.add_parser("report")
    report.add_argument("--surname", required=True)
    report.set_defaults(func=cmd_report)

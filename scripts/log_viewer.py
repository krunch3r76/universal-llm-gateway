#!/usr/bin/env python3
"""
NDJSON Log Viewer — colorized, filterable log viewer for universal_logging output.

Usage:
    python scripts/log_viewer.py /tmp/logs/gateway.log
    python scripts/log_viewer.py --tail /tmp/logs/gateway.log
    python scripts/log_viewer.py --tail --level ERROR /tmp/logs/gateway.log
    python scripts/log_viewer.py --help
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

# ANSI color codes
COLORS = {
    "DEBUG": "\033[37m",  # Gray
    "INFO": "\033[32m",  # Green
    "WARN": "\033[33m",  # Yellow
    "WARNING": "\033[33m",  # Yellow
    "ERROR": "\033[31m",  # Red
    "CRITICAL": "\033[91m",  # Bright red
}
RESET = "\033[0m"
DIM = "\033[2m"


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    ansi_pattern = re.compile(r"\033\[[0-9;]*m")
    return ansi_pattern.sub("", text)


def format_record(record: dict) -> str:
    """Format a single log record for display."""
    ts = record.get("@timestamp", "?")
    level = record.get("level", "?").upper()
    logger = record.get("logger", "?")
    msg = record.get("message", "")

    color = COLORS.get(level, "")

    # Truncate logger name for display
    if len(logger) > 30:
        logger = "..." + logger[-27:]

    return f"{DIM}{ts}{RESET} {color}{level:<8}{RESET} [{logger}] {msg}"


def tail_file(path: Path, level_filter: str | None = None) -> None:
    """Tail a log file, printing new lines as they appear."""
    with open(path) as f:
        # Seek to end
        f.seek(0, 2)

        while True:
            line = f.readline()
            if not line:
                time.sleep(0.1)
                continue

            # Strip ANSI codes before parsing JSON
            clean_line = strip_ansi(line)

            try:
                record = json.loads(clean_line)
            except json.JSONDecodeError:
                continue  # Skip malformed lines

            if level_filter:
                if record.get("level", "").upper() != level_filter.upper():
                    continue

            print(format_record(record), flush=True)


def read_file(path: Path, level_filter: str | None = None) -> None:
    """Read entire log file and print formatted output."""
    with open(path) as f:
        for line in f:
            # Strip ANSI codes before parsing JSON
            clean_line = strip_ansi(line)

            try:
                record = json.loads(clean_line)
            except json.JSONDecodeError:
                continue

            if level_filter:
                if record.get("level", "").upper() != level_filter.upper():
                    continue

            print(format_record(record), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="View and filter NDJSON logs from universal_logging",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s /tmp/logs/gateway.log                    # Print all logs
  %(prog)s --tail /tmp/logs/gateway.log             # Live tail
  %(prog)s --tail --level ERROR /tmp/logs/gateway.log  # Tail errors only
  %(prog)s --level WARNING /tmp/logs/errors.log    # Filter by level
        """,
    )
    parser.add_argument("logfile", type=Path, help="Path to NDJSON log file")
    parser.add_argument(
        "--tail",
        "-f",
        action="store_true",
        help="Follow file (like tail -f)",
    )
    parser.add_argument(
        "--level",
        "-l",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Filter by log level",
    )

    args = parser.parse_args()

    if not args.logfile.exists():
        print(f"Error: {args.logfile} does not exist", file=sys.stderr)
        return 1

    try:
        if args.tail:
            tail_file(args.logfile, args.level)
        else:
            read_file(args.logfile, args.level)
    except KeyboardInterrupt:
        print()  # Clean exit on Ctrl+C
    except BrokenPipeError:
        # Handle pipe closure (e.g., piping to head)
        sys.stderr.close()
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Enhanced NDJSON log viewer with interactive controls.

Features:
- Readable single-line format
- Color coding by level
- Live tail mode
- Filtering by level, logger pattern
- Pager integration (less)
"""

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

# ANSI color codes
COLORS = {
    "DEBUG": "\033[90m",  # Bright black (gray)
    "INFO": "\033[32m",  # Green
    "WARN": "\033[33m",  # Yellow
    "WARNING": "\033[33m",  # Yellow
    "ERROR": "\033[31m",  # Red
    "CRITICAL": "\033[91m",  # Bright red
}
RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    ansi_pattern = re.compile(r"\033\[[0-9;]*m")
    return ansi_pattern.sub("", text)


def format_timestamp(ts: str) -> str:
    """Format timestamp to be more compact."""
    # From: 2026-01-24T22:31:43.375+00:00
    # To: 22:31:43.375 (just time)
    try:
        return ts.split("T")[1].split("+")[0]
    except (IndexError, AttributeError):
        return ts


def format_record(record: dict, compact: bool = False) -> str:
    """Format a single log record for display."""
    ts = record.get("@timestamp", "?")
    level = record.get("level", "?").upper()
    logger = record.get("logger", "?")
    msg = record.get("message", "")

    color = COLORS.get(level, "")
    
    if compact:
        ts_display = format_timestamp(ts)
    else:
        ts_display = ts

    # Truncate logger name for display
    if len(logger) > 40:
        logger = "..." + logger[-37:]

    # Format: timestamp LEVEL [logger] message
    return f"{DIM}{ts_display}{RESET} {color}{level:<8}{RESET} {DIM}[{logger}]{RESET} {msg}"


def tail_file(path: Path, level_filter: str | None = None, logger_filter: str | None = None, compact: bool = False) -> None:
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
            
            if logger_filter:
                if logger_filter.lower() not in record.get("logger", "").lower():
                    continue

            print(format_record(record, compact=compact), flush=True)


def read_file(path: Path, level_filter: str | None = None, logger_filter: str | None = None, compact: bool = False) -> None:
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
            
            if logger_filter:
                if logger_filter.lower() not in record.get("logger", "").lower():
                    continue

            print(format_record(record, compact=compact), flush=True)


def read_file_with_pager(path: Path, level_filter: str | None = None, logger_filter: str | None = None, compact: bool = False) -> None:
    """Read file and pipe to less for scrolling."""
    try:
        # Start less with color support and line wrapping
        less = subprocess.Popen(
            ["less", "-R", "-S"],  # -R for color, -S for no wrap (toggle with -S in less)
            stdin=subprocess.PIPE,
            text=True
        )
        
        with open(path) as f:
            for line in f:
                clean_line = strip_ansi(line)
                try:
                    record = json.loads(clean_line)
                except json.JSONDecodeError:
                    continue

                if level_filter:
                    if record.get("level", "").upper() != level_filter.upper():
                        continue
                
                if logger_filter:
                    if logger_filter.lower() not in record.get("logger", "").lower():
                        continue

                formatted = format_record(record, compact=compact)
                try:
                    less.stdin.write(formatted + "\n")
                except BrokenPipeError:
                    break
        
        less.stdin.close()
        less.wait()
    except KeyboardInterrupt:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Enhanced NDJSON log viewer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s log.txt                           # View with pager (less)
  %(prog)s --tail log.txt                    # Live tail
  %(prog)s --level ERROR log.txt             # Only errors
  %(prog)s --logger federation log.txt       # Only federation logs
  %(prog)s --compact log.txt                 # Compact timestamps (time only)
  %(prog)s --no-pager log.txt                # Print to stdout
        """,
    )
    parser.add_argument("logfile", type=Path, help="Path to NDJSON log file")
    parser.add_argument(
        "--tail", "-f",
        action="store_true",
        help="Follow file (like tail -f)",
    )
    parser.add_argument(
        "--level", "-l",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Filter by log level",
    )
    parser.add_argument(
        "--logger",
        help="Filter by logger name (case-insensitive substring match)",
    )
    parser.add_argument(
        "--compact", "-c",
        action="store_true",
        help="Show compact timestamps (time only, no date)",
    )
    parser.add_argument(
        "--no-pager",
        action="store_true",
        help="Don't use pager (print to stdout)",
    )

    args = parser.parse_args()

    if not args.logfile.exists():
        print(f"Error: {args.logfile} does not exist", file=sys.stderr)
        return 1

    try:
        if args.tail:
            tail_file(args.logfile, args.level, args.logger, args.compact)
        elif args.no_pager or not sys.stdout.isatty():
            read_file(args.logfile, args.level, args.logger, args.compact)
        else:
            read_file_with_pager(args.logfile, args.level, args.logger, args.compact)
    except KeyboardInterrupt:
        print()  # Clean exit on Ctrl+C
    except BrokenPipeError:
        # Handle pipe closure (e.g., piping to head)
        sys.stderr.close()
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())

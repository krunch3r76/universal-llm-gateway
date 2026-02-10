#!/usr/bin/env python3
"""
GUI Log Monitor

Real-time monitoring of GUI logs for debugging and troubleshooting.
Provides colored output and filtering options for better visibility.
"""

import argparse
import sys
import time
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def colorize_log_line(line: str) -> str:
    """Add colors to log lines based on log level"""
    if "[ERROR]" in line:
        return f"\033[91m{line}\033[0m"  # Red
    elif "[WARNING]" in line:
        return f"\033[93m{line}\033[0m"  # Yellow
    elif "[INFO]" in line:
        return f"\033[92m{line}\033[0m"  # Green
    elif "[DEBUG]" in line:
        return f"\033[96m{line}\033[0m"  # Cyan
    else:
        return line


def monitor_log_file(
    log_file: Path, follow: bool = True, filter_patterns: list[str] | None = None
):
    """Monitor a log file with optional filtering"""
    if not log_file.exists():
        print(f"❌ Log file not found: {log_file}")
        return

    print(f"📊 Monitoring GUI log: {log_file}")
    print("=" * 80)

    # Read existing content
    with open(log_file) as f:
        lines = f.readlines()
        for line in lines[-20:]:  # Show last 20 lines
            if filter_patterns:
                if not any(
                    pattern.lower() in line.lower() for pattern in filter_patterns
                ):
                    continue
            print(colorize_log_line(line.rstrip()))

    if follow:
        print("\n🔄 Following new log entries (Ctrl+C to stop)...")
        print("=" * 80)

        # Follow new entries
        with open(log_file) as f:
            f.seek(0, 2)  # Go to end of file

            try:
                while True:
                    line = f.readline()
                    if line:
                        if filter_patterns:
                            if not any(
                                pattern.lower() in line.lower()
                                for pattern in filter_patterns
                            ):
                                continue
                        print(colorize_log_line(line.rstrip()))
                    else:
                        time.sleep(0.1)
            except KeyboardInterrupt:
                print("\n👋 Stopped monitoring")


def main():
    parser = argparse.ArgumentParser(description="Monitor GUI logs for debugging")
    parser.add_argument(
        "--log-file",
        choices=["gui", "gui_debug", "gui_chunk_debug", "all"],
        default="gui",
        help="Which GUI log file to monitor",
    )
    parser.add_argument(
        "--no-follow",
        action="store_true",
        help="Show existing content only, don't follow new entries",
    )
    parser.add_argument(
        "--filter",
        nargs="*",
        help="Filter log entries containing these patterns (case-insensitive)",
    )
    parser.add_argument(
        "--errors-only", action="store_true", help="Show only ERROR and WARNING entries"
    )

    args = parser.parse_args()

    # Determine log files to monitor
    log_files = []
    logs_dir = project_root / "logs"

    if args.log_file == "all":
        log_files = [
            logs_dir / "gui.log",
            logs_dir / "gui_debug.log",
            logs_dir / "gui_chunk_debug.log",
        ]
    else:
        log_files = [logs_dir / f"{args.log_file}.log"]

    # Set up filtering
    filter_patterns = args.filter or []
    if args.errors_only:
        filter_patterns.extend(["ERROR", "WARNING", "Exception", "Failed", "Error"])

    # Monitor each log file
    for log_file in log_files:
        if log_file.exists():
            monitor_log_file(
                log_file, follow=not args.no_follow, filter_patterns=filter_patterns
            )
            if len(log_files) > 1:
                print("\n" + "=" * 80 + "\n")


if __name__ == "__main__":
    main()

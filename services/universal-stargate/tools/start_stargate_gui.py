#!/usr/bin/env python3
"""
Standalone launcher for the stargate GUI.

Usage:
    # Unix socket (local monitoring)
    python tools/start_stargate_gui.py
    python tools/start_stargate_gui.py --transport unix --unix-socket /path/to/socket.sock

    # TCP (remote monitoring)
    python tools/start_stargate_gui.py --transport tcp --host <server-ip> --port 9997
    python tools/start_stargate_gui.py --transport tcp --host localhost --port 9997

    # Debug mode
    python tools/start_stargate_gui.py --debug
"""

import argparse
import os
import sys

from universal_logging import get_logger

# Add the project root to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# MONKEY PATCH: Configure JSON to use unicode by default
import json

_original_dumps = json.dumps


def unicode_friendly_dumps(obj, **kwargs):
    """JSON dumps with ensure_ascii=False by default."""
    if "ensure_ascii" not in kwargs:
        kwargs["ensure_ascii"] = False
    return _original_dumps(obj, **kwargs)


json.dumps = unicode_friendly_dumps

# Import the main application
from gui.main import main


def setup_debug_logging():
    """Setup debug logging for GUI debugging"""
    # Note: log_file path will be handled by load_logging_config() using DATA_DIR
    from config.logging_config import load_logging_config

    load_logging_config()

    # Enable debug logging for specific components
    get_logger("gui.model.network_receiver").setLevel("DEBUG")
    get_logger("gui.controller.event_controller").setLevel("DEBUG")
    get_logger("gui.model.response_parser").setLevel("DEBUG")
    get_logger("gui.view.components.stream_display").setLevel("DEBUG")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Universal Stargate GUI")
    parser.add_argument(
        "--transport",
        type=str,
        choices=["unix", "tcp"],
        default="unix",
        help="Transport type: unix or tcp (default: unix)",
    )
    parser.add_argument(
        "--unix-socket",
        type=str,
        help="Unix socket path (default: /tmp/stargate_events.sock)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="localhost",
        help="TCP host for remote monitoring (default: localhost)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=9997,
        help="TCP port for remote monitoring (default: 9997)",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    if args.debug:
        setup_debug_logging()
        print("🔍 Debug mode enabled - GUI will log detailed information")
        data_dir = os.environ.get("DATA_DIR", "/tmp")
        log_dir = os.path.join(data_dir, "logs", "universal-stargate")
        print(f"📝 Debug logs will be written to {log_dir}/gui_debug.log")

    # Override sys.argv to pass arguments to main()
    sys.argv = ["start_stargate_gui.py"]
    sys.argv.extend(["--transport", args.transport])
    if args.unix_socket:
        sys.argv.extend(["--unix-socket", args.unix_socket])
    sys.argv.extend(["--host", args.host])
    sys.argv.extend(["--port", str(args.port)])
    if args.debug:
        sys.argv.append("--debug")

    main()

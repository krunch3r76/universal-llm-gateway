#!/usr/bin/env python3
"""DEPRECATED shim — delegates to ``mcp-fastmcp-remote-bridge.py`` (Track 3).

Steady-state Cursor uses direct HTTPS MCP. Legacy ``mcp.json`` fallback entries
that still point at this path are forwarded to the maintained bridge.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_BRIDGE = Path(__file__).resolve().parent / "mcp-fastmcp-remote-bridge.py"


def main() -> None:
    print(
        "mcp-stdio-proxy is removed (2026-06-17): delegating to "
        "mcp-fastmcp-remote-bridge (fastmcp-remote). Update mcp.json to point "
        "at the bridge directly.",
        file=sys.stderr,
        flush=True,
    )
    if not _BRIDGE.is_file():
        print(f"mcp-stdio-proxy shim error: bridge missing: {_BRIDGE}", file=sys.stderr)
        raise SystemExit(2)
    os.execv(sys.executable, [sys.executable, str(_BRIDGE), *sys.argv[1:]])


if __name__ == "__main__":
    main()

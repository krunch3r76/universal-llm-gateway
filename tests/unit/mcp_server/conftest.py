"""Bind ``tools`` to services/mcp-server when collecting this directory."""

from __future__ import annotations

import sys
from pathlib import Path

_MCP_SERVER_ROOT = Path(__file__).resolve().parents[3] / "services" / "mcp-server"
_root = str(_MCP_SERVER_ROOT)
if _root in sys.path:
    sys.path.remove(_root)
sys.path.insert(0, _root)

_tools = sys.modules.get("tools")
if _tools is not None:
    _tools_file = (getattr(_tools, "__file__", None) or "").replace("\\", "/")
    if "mcp-server" not in _tools_file:
        for key in list(sys.modules):
            if key == "tools" or key.startswith("tools."):
                del sys.modules[key]

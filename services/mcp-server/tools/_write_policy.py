"""MCP project write policy — single source of truth.

Exposes whether MCP tools are permitted to write to the workspaces sandbox.
Controlled by MCP_PROJECT_WRITE_ENABLED (set by the env producer from
project_access: rw in ~/.gateway/mcp.yaml).

Toggle: set project_access: rw in ~/.gateway/mcp.yaml and sync_restart mcp.
"""

from __future__ import annotations

import os


def project_writes_enabled() -> bool:
    """Return True when MCP tools may write to the workspaces sandbox."""
    val = os.environ.get("MCP_PROJECT_WRITE_ENABLED", "false").strip().lower()
    return val in {"1", "true", "yes", "on"}


def project_write_denied_error() -> dict[str, str]:
    """Standard error dict for blocked write ops."""
    return {
        "error": (
            "project writes disabled "
            "(set project_access: rw in ~/.gateway/mcp.yaml and sync_restart mcp)"
        )
    }

"""Tasks root configuration and traversal protection.

Provides the canonical TASKS_ROOT and safe_tasks_path() guard used by all
context tool registrars.
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp_events import record

TASKS_ROOT = Path(os.environ.get("TASKS_ROOT", "/data/tasks"))


def safe_tasks_path(relative: str) -> Path:
    """Resolve *relative* inside the tasks root, rejecting traversal."""
    clean = relative.lstrip("/")
    resolved_root = TASKS_ROOT.resolve()
    candidate = (resolved_root / clean).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError:
        record("mcp.tool.path.traversal.rejected", path=relative)
        raise ValueError(
            f"Path {relative!r} resolves outside tasks root; traversal rejected"
        )
    return candidate

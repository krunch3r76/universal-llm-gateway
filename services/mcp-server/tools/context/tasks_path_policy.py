"""Tasks root configuration, read-only policy, traversal protection, and violation telemetry.

Provides the canonical TASKS_ROOT, TASKS_READ_ONLY flag, and safe_tasks_path()
guard used by all context tool registrars. Read-only violations are recorded via
mcp_events for telemetry.
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp_events import record

TASKS_ROOT = Path(os.environ.get("TASKS_ROOT", "/data/tasks"))

TASKS_READ_ONLY = os.environ.get("TASKS_READ_ONLY", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def read_only_error() -> dict[str, str]:
    return {
        "error": (
            "tasks context is read-only (TASKS_READ_ONLY=true); "
            "write tools are disabled"
        )
    }


def record_read_only_violation(
    *,
    tool: str,
    path: str | None = None,
    operation: str | None = None,
) -> None:
    payload: dict[str, str] = {"tool": tool}
    if path is not None:
        payload["path"] = path
    if operation is not None:
        payload["operation"] = operation
    record("mcp.tool.read.only.violation", **payload)


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

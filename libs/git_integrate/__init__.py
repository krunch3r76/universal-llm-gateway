"""Git integration library — atomic arc-worktree merge mechanics."""

from __future__ import annotations

import importlib
from typing import Any

# Eager import of integrate/land/commit pulls ops_common, which imports
# services.git_integration_worker — absent on MCP PYTHONPATH (libs + mcp-server).
_LAZY_ATTRS: dict[str, tuple[str, str]] = {
    "CasResult": ("git_integrate.schema", "CasResult"),
    "CommitResult": ("git_integrate.schema", "CommitResult"),
    "IntegrateResult": ("git_integrate.schema", "IntegrateResult"),
    "MergeResult": ("git_integrate.schema", "MergeResult"),
    "GitIntegrateCompleted": ("git_integrate.events", "GitIntegrateCompleted"),
    "GitIntegrateGateFailed": ("git_integrate.events", "GitIntegrateGateFailed"),
    "GitIntegrateRejected": ("git_integrate.events", "GitIntegrateRejected"),
    "GitIntegrateRequested": ("git_integrate.events", "GitIntegrateRequested"),
    "GitIntegrateRetried": ("git_integrate.events", "GitIntegrateRetried"),
    "GitLogRead": ("git_integrate.events", "GitLogRead"),
    "GitStatusRead": ("git_integrate.events", "GitStatusRead"),
    "emit_git_integrate_completed": (
        "git_integrate.events",
        "emit_git_integrate_completed",
    ),
    "emit_git_integrate_gate_failed": (
        "git_integrate.events",
        "emit_git_integrate_gate_failed",
    ),
    "emit_git_integrate_rejected": (
        "git_integrate.events",
        "emit_git_integrate_rejected",
    ),
    "emit_git_integrate_requested": (
        "git_integrate.events",
        "emit_git_integrate_requested",
    ),
    "emit_git_integrate_retried": (
        "git_integrate.events",
        "emit_git_integrate_retried",
    ),
    "emit_git_log_read": ("git_integrate.events", "emit_git_log_read"),
    "emit_git_status_read": ("git_integrate.events", "emit_git_status_read"),
    "register_uds_publisher": ("git_integrate.events", "register_uds_publisher"),
    "commit_op": ("git_integrate.commit", "commit_op"),
    "integrate_op": ("git_integrate.integrate", "integrate_op"),
    "land_op": ("git_integrate.land", "land_op"),
}

__all__ = [
    "CasResult",
    "CommitResult",
    "GitIntegrateCompleted",
    "GitIntegrateGateFailed",
    "GitIntegrateRejected",
    "GitIntegrateRequested",
    "GitIntegrateRetried",
    "GitLogRead",
    "GitStatusRead",
    "IntegrateResult",
    "MergeResult",
    "emit_git_integrate_completed",
    "emit_git_integrate_gate_failed",
    "emit_git_integrate_rejected",
    "emit_git_integrate_requested",
    "emit_git_integrate_retried",
    "emit_git_log_read",
    "emit_git_status_read",
    "commit_op",
    "integrate_op",
    "land_op",
    "register_uds_publisher",
]


def __getattr__(name: str) -> Any:
    if name in _LAZY_ATTRS:
        module_path, attr = _LAZY_ATTRS[name]
        value = getattr(importlib.import_module(module_path), attr)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
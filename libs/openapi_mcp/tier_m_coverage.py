"""Tier-M manifest coverage drift — reuses two-tier ManifestCheckResult shape."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from openapi_mcp.codegen import ManifestCheckResult

_PIPELINE_OPS: frozenset[str] = frozenset(
    {"run", "async", "result", "validate", "stats", "cancel"}
)
_OBSERVABILITY_OPS: frozenset[str] = frozenset({"query"})


def _ensure_mcp_server_on_path() -> None:
    repo = Path(__file__).resolve().parents[2]
    mcp_server = str(repo / "services" / "mcp-server")
    if mcp_server not in sys.path:
        sys.path.insert(0, mcp_server)

_PIPELINE_OPS: frozenset[str] = frozenset(
    {"run", "async", "result", "validate", "stats", "cancel"}
)
_OBSERVABILITY_OPS: frozenset[str] = frozenset({"query"})


@dataclass(frozen=True, slots=True)
class TierMDriftReport:
    """Counts and messages from a tier-M manifest vs served-op comparison."""

    manifest_row_count: int
    served_op_count: int
    fatal_messages: tuple[str, ...]
    warning_messages: tuple[str, ...]

    @property
    def check_result(self) -> ManifestCheckResult:
        return ManifestCheckResult(
            fatal_messages=self.fatal_messages,
            warning_messages=self.warning_messages,
        )


def _manifest_rows():
    from services.git_integration_worker.cursor_auto.tier_m_manifest import (
        DEFAULT_MANIFEST,
        ManifestRow,
    )

    return DEFAULT_MANIFEST


def _row_covers(rows: Iterable, tool: str, op: str) -> bool:
    for row in rows:
        if row.tool == tool and (row.op == op or row.wildcard):
            return True
    return False


def collect_served_tool_ops() -> dict[str, frozenset[str]]:
    """Return served MCP tool.op pairs for tier-M manifest tools."""
    _ensure_mcp_server_on_path()
    from cortex_store.dispatch_ops import _OP_SPECS
    from tools.filesystem._fs_dispatch import OP_SANDBOXES
    from tools.local._email_catalog import CATALOG
    from tools.manage import _VALID_ACTIONS

    return {
        "cortex": frozenset(_OP_SPECS),
        "email": frozenset(CATALOG),
        "fs": frozenset(OP_SANDBOXES),
        "manage": frozenset(_VALID_ACTIONS),
        "observability": _OBSERVABILITY_OPS,
        "pipeline": _PIPELINE_OPS,
    }


def check_tier_m_manifest_coverage() -> TierMDriftReport:
    """Compare tier-M manifest rows against served MCP tool ops (two-tier)."""
    rows = _manifest_rows()
    served_by_tool = collect_served_tool_ops()
    manifest_tools = {row.tool for row in rows}

    fatal: list[str] = []
    for row in rows:
        if row.wildcard:
            continue
        served = served_by_tool.get(row.tool, frozenset())
        if row.op not in served:
            fatal.append(
                f"FATAL: tier-M manifest row {row.tool_op!r} has no served operation"
            )

    warnings: list[str] = []
    for tool, ops in sorted(served_by_tool.items()):
        if tool not in manifest_tools:
            continue
        for op in sorted(ops):
            if _row_covers(rows, tool, op):
                continue
            warnings.append(
                f"WARNING: served tier-M-eligible op {tool}.{op} has no manifest row"
            )

    manifest_rows = sum(1 for row in rows if not row.wildcard)
    served_ops = sum(len(ops) for tool, ops in served_by_tool.items() if tool in manifest_tools)
    return TierMDriftReport(
        manifest_row_count=manifest_rows,
        served_op_count=served_ops,
        fatal_messages=tuple(fatal),
        warning_messages=tuple(warnings),
    )


__all__ = [
    "TierMDriftReport",
    "check_tier_m_manifest_coverage",
    "collect_served_tool_ops",
]

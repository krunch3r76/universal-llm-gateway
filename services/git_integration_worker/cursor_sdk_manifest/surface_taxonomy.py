"""Op/tool vocabularies and surface classification for the effects manifest.

Owns every module-level capture constant from the pre-split monolith
(``CaptureBranch``, repo/MCP/cortex/fs/rag/service frozensets, surface order,
body-probe/detail caps) plus the two classifiers ``_surface_for_mcp_tool`` and
``_surface_coverage``. This module is an intra-package leaf: it imports no
sibling. ``_CORTEX_WRITE_OPS`` must remain defined here — after the split,
``libs/unearned_self_assertion_auditor/coats.py`` AST-reads this file for the
coat_three write-op frozenset. ``_REPO_FILE_OPS`` / ``_REPO_WRITE_OPS`` are
re-exported from ``__init__`` because ``cursor_sdk_capture_status`` imports
them by those names. Do not hoist ``SUBAGENTS_SURFACE``; import it from
``cursor_sdk_subagent_capture`` the same way the monolith does.
"""

from __future__ import annotations

import re
from typing import Literal, TypedDict

from implement_admission.closeout_models import SurfaceSection

from services.git_integration_worker.cursor_sdk_subagent_capture import (
    SUBAGENTS_SURFACE,
)

CaptureBranch = Literal["A", "B", "NO_CAPTURE"]


class DroppedNonFileEntry(TypedDict):
    surface: str
    op: str
    target: str
    reason: str


DetailCap = 500
ResultCap = 2000
MAX_MANIFEST_BODY_PROBE = 4_000

_REPO_READ_OPS = frozenset({"observed"})
_REPO_WRITE_OPS = frozenset({"write", "edit", "delete"})
_REPO_FILE_OPS = _REPO_WRITE_OPS | _REPO_READ_OPS
_REPO_LABEL_OPS = _REPO_WRITE_OPS
_REPO_SHELL_OP = "shell"
_MCP_OP = "mcp"
_VORTEX_SERVER = "user-vortex"

_CORTEX_TOOLS = frozenset({"cortex", "cortex_brief"})
_CORTEX_WRITE_OPS = frozenset({"assert", "supersede", "observe", "friction"})
_ASSERTION_IDENTITY_RE = re.compile(r"^assertion:(\d+)$")
_AGENT_BUS_TOOLS = frozenset({"agent_bus", "agent_bus_read"})
_FS_TOOLS = frozenset({"fs"})
_FS_WRITE_OPS = frozenset(
    {
        "write",
        "append",
        "prepend",
        "insert_at_line",
        "replace",
        "md_replace",
        "md_append",
        "md_insert",
        "write_binary",
        "append_binary",
        "copy",
        "move",
    }
)
_RAG_TOOLS = frozenset({"rag"})
_SERVICE_TOOLS = frozenset(
    {
        "manage",
        "pipeline",
        "observability",
        "team_dispatch",
        "panel_dispatch",
        "retrieve",
        "tool_search",
        "dispatch",
    }
)
_PLUMBING_SURFACES = frozenset({"cortex", "agent_bus", "service"})
# Boundary surfaces preserved when git diff is empty — plumbing collapse must not
# erase nested/cortex-only capture (AC-9j).
_PRESERVE_ON_NO_CODE_CHANGE_SURFACES = frozenset(
    {"cortex", "fs", "agent_bus", "rag", SUBAGENTS_SURFACE}
)
_SURFACE_ORDER = ("repo", "cortex", "agent_bus", "fs", "rag", "service", "subagents")
def _surface_for_mcp_tool(tool_name: str) -> str:
    if tool_name in _CORTEX_TOOLS:
        return "cortex"
    if tool_name in _AGENT_BUS_TOOLS:
        return "agent_bus"
    if tool_name in _FS_TOOLS:
        return "fs"
    if tool_name in _RAG_TOOLS:
        return "rag"
    return "service"


def _surface_coverage(section: SurfaceSection) -> str:
    if section.cross_check:
        return "partial"
    return "complete"

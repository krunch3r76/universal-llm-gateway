"""Read-only companion to the unified ``fs`` tool (Orion approval friction).

Mirrors ``agent_bus_read``: separate registration with ``readOnlyHint=True``,
exact op whitelist, delegation to ``fs_impl`` after permission checks.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from endpoint_surface import Surface
from fs_impl import fs_impl
from fs_roots import LIFE_WORKSPACES_READ_OPS
from mcp.types import ToolAnnotations
from mcp_events import record
from mcp_toolprogress import toolprogress_begin, toolprogress_end
from request_profile import current_request_metadata

if TYPE_CHECKING:
    from fastmcp import FastMCP

# Exact whitelist from orion-readonly-mcp-surfaces G3 frame.
FS_READ_OPS: frozenset[str] = frozenset(
    {
        "read",
        "read_multi",
        "list",
        "search",
        "md_list",
        "md_read",
        "md_to_dict",
        "resolve_sha256",
        "find",
        "recent_commits",
    }
)
FS_READ_CORTEX_ONLY: frozenset[str] = frozenset({"resolve_sha256"})
FS_READ_WORKSPACES_ONLY: frozenset[str] = frozenset({"find", "recent_commits"})

FS_READ_REJECTED_WRITE_OPS: frozenset[str] = frozenset(
    {
        "write",
        "append",
        "prepend",
        "replace",
        "insert_at_line",
        "delete",
        "move",
        "copy",
        "write_binary",
        "append_binary",
        "md_replace",
        "md_rewrite_section",
        "md_append",
        "md_insert",
        "md_delete",
    }
)

_OVERFLOW_REGISTRY: dict[str, Callable[..., Any]] | None = None


def bind_fs_read_overflow(registry: dict[str, Callable[..., Any]]) -> None:
    """Bind post-prune overflow callables (markdown) for fs_read md_* ops."""
    global _OVERFLOW_REGISTRY
    _OVERFLOW_REGISTRY = registry


def _not_a_read_op(op: str) -> dict[str, str]:
    return {
        "error": (
            f"fs_read: {op!r} is not a read-only op. "
            f"Available: {sorted(FS_READ_OPS)}. "
            "For mutating ops use the fs tool."
        ),
        "reason": "not_a_read_op",
    }


def _companion_sandbox_refusal(op: str, sandbox: str) -> dict[str, str] | None:
    if op in FS_READ_CORTEX_ONLY and sandbox == "workspaces":
        return _not_a_read_op(op)
    if op in FS_READ_WORKSPACES_ONLY and sandbox == "cortex":
        return _not_a_read_op(op)
    return None


def register_fs_read_tool(mcp: FastMCP, surface: Surface) -> None:
    """Register read-only ``fs_read`` with the same wire shape as ``fs``."""

    @mcp.tool(
        title="File I/O (read-only)",
        annotations=ToolAnnotations(readOnlyHint=True),
    )
    def fs_read(
        op: str,
        sandbox: str = "",
        description: str = "",
        path: str = "",
        paths: list[str] | None = None,
        content: str = "",
        target: str = "",
        target_sandbox: str = "",
        line: int = 0,
        section: str = "",
        all_occurrences: bool = False,
        include_untracked: bool = True,
        binary: bool = False,
        max_depth: int = 3,
        offset: int = 0,
        limit: int = 0,
        expected_sha256: str = "",
        if_absent: bool = False,
        heading: str = "",
        level: int = 0,
        position: str = "",
        mode: str = "",
        thread: str | None = None,
        since: str = "",
        include_worktrees: bool = False,
        size_only: bool = False,
    ) -> dict[str, Any]:
        """Read-only fs ops — auto-approvable companion to fs.

        Same parameters as fs; only non-mutating ops are admitted.
        For write/delete/move/copy use fs.
        """
        op_name = str(op or "").strip()
        if op_name not in FS_READ_OPS:
            record(
                "mcp.fs_read.rejected",
                op=op_name,
                reason="not_a_read_op",
                surface=surface,
            )
            return _not_a_read_op(op_name or op)

        meta = current_request_metadata()
        req_surface = str(meta.get("surface") or surface)
        effective_sandbox = str(sandbox or "").strip()
        if not effective_sandbox and path.startswith(("cortex://", "workspaces://")):
            effective_sandbox = "cortex" if path.startswith("cortex://") else "workspaces"
        if not effective_sandbox and req_surface == "life":
            effective_sandbox = "cortex"

        sandbox_refusal = _companion_sandbox_refusal(op_name, effective_sandbox)
        if sandbox_refusal is not None:
            return sandbox_refusal

        if req_surface == "life" and effective_sandbox == "workspaces":
            if op_name not in LIFE_WORKSPACES_READ_OPS:
                record(
                    "mcp.fs_read.rejected",
                    op=op_name,
                    reason="life_workspaces_grant",
                    surface=req_surface,
                )
                return {
                    "error": (
                        f"fs_read: op={op_name!r} is not in the life workspaces "
                        f"read grant. Permitted: {sorted(LIFE_WORKSPACES_READ_OPS)}."
                    ),
                    "reason": "not_a_read_op",
                }

        t_prog, prog_timer = toolprogress_begin("fs_read", op=op_name)
        err: str | None = None
        try:
            registry = _OVERFLOW_REGISTRY if _OVERFLOW_REGISTRY is not None else {}
            return fs_impl(
                surface=req_surface,  # type: ignore[arg-type]
                overflow_registry=registry,
                op=op_name,
                sandbox=sandbox,
                path=path,
                paths=paths,
                content=content,
                target=target,
                target_sandbox=target_sandbox,
                line=line,
                section=section,
                all_occurrences=all_occurrences,
                include_untracked=include_untracked,
                binary=binary,
                max_depth=max_depth,
                offset=offset,
                limit=limit,
                expected_sha256=expected_sha256,
                if_absent=if_absent,
                heading=heading,
                level=level,
                position=position,
                mode=mode,
                thread=thread,
                since=since,
                include_worktrees=include_worktrees,
                size_only=size_only,
            )
        except Exception as exc:
            err = str(exc) or type(exc).__name__
            return {"error": str(exc), "reason": "handler_error"}
        finally:
            toolprogress_end(t_prog, prog_timer, "fs_read", error=err, op=op_name)

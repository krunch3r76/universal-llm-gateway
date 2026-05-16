"""grok_build MCP tool — top-level op dispatcher for headless grok CLI work.

Routes to per-op handlers:
- ``op="dispatch"`` → ``_grok_build_dispatch.dispatch_op`` (CLI invocation)
- ``op="worktree_create"`` → ``_grok_build_worktree.worktree_create_op``
- ``op="worktree_remove"`` → ``_grok_build_worktree.worktree_remove_op``

Unknown ops reject at the top level with ``reason_code="unknown_op"`` and emit
``mcp.grok.build.dispatch.rejected`` (the dispatch family carries the
catch-all for malformed entry calls).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Literal

from tools._grok_build_dispatch import dispatch_op
from tools._grok_build_envelope import _envelope_rejected
from tools._grok_build_events import emit_grok_build_dispatch_rejected
from tools._grok_build_worktree import worktree_create_op
from tools._grok_build_worktree_remove import worktree_remove_op

if TYPE_CHECKING:
    from fastmcp import FastMCP


async def grok_build(
    op: Literal["dispatch", "worktree_create", "worktree_remove"],
    cwd: str = "",
    prompt: str = "",
    *,
    mode: Literal["read_only", "edit"] = "read_only",
    system_context: str | None = None,
    model: str | None = None,
    session_id: str | None = None,
    continue_recent: bool = False,
    output_format: Literal["json", "streaming-json"] = "json",
    timeout_seconds: int = 900,
    name: str = "",
    branch: str = "",
    source_repo: str = "",
) -> dict[str, Any]:
    """Dispatch grok_build op to the matching handler.

    ``op="dispatch"`` takes (cwd, prompt) positionally plus all dispatch
    kwargs; ``op="worktree_create"`` takes (name, branch, source_repo);
    ``op="worktree_remove"`` takes (name). The signature unions all params
    so the MCP schema exposes one tool with op-conditional fields.
    """
    if op == "dispatch":
        return await dispatch_op(
            cwd,
            prompt,
            mode=mode,
            system_context=system_context,
            model=model,
            session_id=session_id,
            continue_recent=continue_recent,
            output_format=output_format,
            timeout_seconds=timeout_seconds,
        )
    if op == "worktree_create":
        return await worktree_create_op(
            name=name, branch=branch, source_repo=source_repo
        )
    if op == "worktree_remove":
        return await worktree_remove_op(name=name)
    dispatch_id = str(uuid.uuid4())
    reason = f"unsupported op: {op!r}"
    emit_grok_build_dispatch_rejected(
        dispatch_id=dispatch_id,
        reason_code="unknown_op",
        reason=reason,
        mode=mode,
        op=op,
        cwd=cwd,
        model=model or "",
    )
    return _envelope_rejected(
        dispatch_id, mode, cwd, session_id, model, "unknown_op", reason
    )


def register_grok_build_tools(mcp: FastMCP) -> None:
    """Mount grok_build on the MCP catalog (decoration-at-register-time)."""
    mcp.tool(title="Grok Build Dispatch")(grok_build)

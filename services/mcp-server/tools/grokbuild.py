"""grokbuild MCP tool — top-level op dispatcher for headless grok CLI work (V1).

Routes to per-op handlers:

* ``op="build"`` → ``_grokbuild_dispatch.dispatch_op`` (CLI invocation)
* ``op="models"`` → registry listing with per-model capability flags
* ``op="worktree_create"`` / ``op="worktree_remove"`` / ``op="worktree_list"``
* ``op="fetch_result"`` (sidecar replay for past dispatch_ids)
* ``op="push"`` / ``op="pr_create"`` (git/PR helpers)

Retired in V1 (uniform .rejected envelope at top level):

* ``op='dispatch'`` → ``retired_op``
* ``output_format='json'`` → ``retired_output_format``
* ``continue_recent=True`` → ``retired_param``

Unknown ops reject at the top level with ``reason_code="unknown_op"`` and
emit ``mcp.grokbuild.dispatch.rejected`` (the dispatch family carries the
catch-all for malformed entry calls).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Literal

from tools._grokbuild_constants import _MODEL_REGISTRY
from tools._grokbuild_dispatch import dispatch_op
from tools._grokbuild_envelope import _envelope_rejected
from tools._grokbuild_events import emit_grok_build_dispatch_rejected
from tools._grokbuild_fetch_result import fetch_result_op
from tools._grokbuild_git_ops import pr_create_op, push_op
from tools._grokbuild_worktree import worktree_create_op
from tools._grokbuild_worktree_list import worktree_list_op
from tools._grokbuild_worktree_remove import worktree_remove_op

if TYPE_CHECKING:
    from fastmcp import FastMCP


async def grokbuild(  # noqa: PLR0913 — wide MCP tool surface by design
    op: str,
    cwd: str = "",
    prompt: str = "",
    *,
    # dispatch ("build") surface — V1.
    mode: Literal["read_only", "edit"] = "read_only",
    system_context: str | None = None,
    model: str | None = None,
    session_id: str | None = None,
    continue_recent: bool = False,  # retired in V1; validator rejects True
    output_format: str = "streaming-json",
    timeout_seconds: int | None = None,
    tier: str = "thorough",
    reasoning_effort: str | None = None,
    effort: str | None = None,
    check: bool | None = None,
    no_subagents: bool = False,
    disable_web_search: bool = False,
    max_turns: int | None = None,
    best_of_n: int | None = None,
    resume_strict: bool = False,
    # worktree ops surface.
    name: str = "",
    branch: str = "",
    source_repo: str = "",
    create_branch: bool = False,
    start_point: str = "",
    # fetch_result surface.
    dispatch_id: str = "",
    format: Literal["json", "text", "summary"] = "json",
    # git ops surface.
    remote: str = "origin",
    set_upstream: bool = True,
    pr_title: str = "",
    pr_body: str = "",
    pr_base: str = "",
    pr_head: str = "",
    draft: bool = False,
) -> dict[str, Any]:
    """Dispatch grokbuild op to the matching handler.

    V1 op set: ``build`` (renamed from ``dispatch``), ``models``,
    ``worktree_create``, ``worktree_remove``, ``worktree_list``,
    ``fetch_result``, ``push``, ``pr_create``.

    Retired in V1 (validator emits structured rejection):

    * ``op='dispatch'`` → ``retired_op`` (use ``op='build'``)
    * ``output_format='json'`` → ``retired_output_format`` (use ``streaming-json``)
    * ``continue_recent=True`` → ``retired_param`` (set ``session_id`` explicitly)

    Tier resolution (only relevant for ``op='build'``): the dispatcher
    overlays preset values onto unspecified params; per-param explicit
    values always win. See ``_grokbuild_dispatch._TIER_PRESETS``.

    Parameter valid values (``op='build'``):

    * ``tier``: ``quick`` | ``balanced`` | ``thorough`` | ``max``
    * ``reasoning_effort``: ``none`` | ``minimal`` | ``low`` | ``medium`` |
      ``high`` | ``xhigh``  — maps to ``grok --reasoning-effort``.
      Note: ``max`` is NOT valid here; use ``tier='max'`` or set
      ``effort='max'`` instead.
    * ``effort``: ``low`` | ``medium`` | ``high`` | ``xhigh`` | ``max``  —
      maps to ``grok --effort``.
      Note: ``none`` and ``minimal`` are NOT valid here; they are only
      accepted by ``reasoning_effort``.
    * ``mode``: ``read_only`` | ``edit``
    """
    if op == "models":
        return _list_models()
    if op == "build":
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
            tier=tier,
            reasoning_effort=reasoning_effort,
            effort=effort,
            check=check,
            no_subagents=no_subagents,
            disable_web_search=disable_web_search,
            max_turns=max_turns,
            best_of_n=best_of_n,
            resume_strict=resume_strict,
        )
    if op == "worktree_create":
        return await worktree_create_op(
            name=name,
            branch=branch,
            source_repo=source_repo,
            create_branch=create_branch,
            start_point=start_point,
        )
    if op == "worktree_remove":
        return await worktree_remove_op(name=name)
    if op == "worktree_list":
        return await worktree_list_op()
    if op == "fetch_result":
        return await fetch_result_op(dispatch_id=dispatch_id, format=format)
    if op == "push":
        return await push_op(
            cwd=cwd, remote=remote, branch=branch, set_upstream=set_upstream
        )
    if op == "pr_create":
        return await pr_create_op(
            cwd=cwd,
            pr_title=pr_title,
            pr_body=pr_body,
            pr_base=pr_base,
            pr_head=pr_head,
            draft=draft,
        )
    # Retired or unknown op — synthesize a rejection envelope at the top level.
    rejection_id = str(uuid.uuid4())
    if op == "dispatch":
        reason_code = "retired_op"
        reason = "op='dispatch' was retired in V1; use op='build'"
    else:
        reason_code = "unknown_op"
        reason = f"unsupported op: {op!r}"
    emit_grok_build_dispatch_rejected(
        dispatch_id=rejection_id,
        reason_code=reason_code,
        reason=reason,
        mode=mode,
        op=op,
        cwd=cwd,
        model=model or "",
    )
    return _envelope_rejected(
        rejection_id, mode, cwd, session_id, model, reason_code, reason
    )


def _list_models() -> dict[str, Any]:
    """Build the op='models' response from the registry + live Stargate config."""
    return {
        "models": [
            {
                "id": model_id,
                "supports_reasoning_effort": caps.supports_reasoning_effort,
                "supports_effort": caps.supports_effort,
                "supports_subagents": caps.supports_subagents,
                "internal_multi_agent": caps.internal_multi_agent,
                "default_reasoning_effort": caps.default_reasoning_effort,
                "notes": caps.notes,
            }
            for model_id, caps in _MODEL_REGISTRY.items()
        ]
    }


def register_grokbuild_tools(mcp: FastMCP) -> None:
    """Mount grokbuild on the MCP catalog (decoration-at-register-time).

    Tool title is "Grok Build" (not "Grok Build Dispatch") — the V1 op set
    covers many flows (build, worktrees, fetch_result, push, pr_create);
    "Dispatch" was vestigial from when there was only the dispatch op.
    """
    mcp.tool(title="Grok Build")(grokbuild)

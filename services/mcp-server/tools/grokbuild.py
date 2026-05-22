"""grokbuild MCP tool — thin relay to grokbuild-worker via Stargate (V2).

Routes every MCP op to the worker's ``/api/v1/grokbuild/*`` REST surface.
The MCP tool descriptor (caller-visible op vocabulary, parameter schemas) is
unchanged from V1 — only the execution host moved to grokbuild-worker.

Retired ops (``op='dispatch'``, unknown ops) are still rejected at the relay
layer to preserve backwards-compatible envelope shapes for callers that
pre-date the V2 cutover.
"""

from __future__ import annotations

import re
import uuid
from typing import TYPE_CHECKING, Any, Literal

import httpx
from transport_utils import DEFAULT_STARGATE_URL, make_async_client
from universal_logging import get_logger

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = get_logger(__name__)

_SYNC_TIMEOUT = 60.0
# POST /dispatches returns 202 immediately; no need for a long budget.
_BUILD_TIMEOUT = 30.0

# Allowed params for op="build" (mirrors GrokbuildDispatchRequest fields).
_BUILD_PARAMS: frozenset[str] = frozenset(
    "cwd prompt mode system_context model session_id continue_recent output_format "
    "timeout_seconds tier reasoning_effort effort check no_subagents "
    "disable_web_search max_turns best_of_n resume_strict".split()
)

# (HTTP method, path template, allowed param names)
_OPS: dict[str, tuple[str, str, frozenset[str]]] = {
    "models": ("GET", "/api/v1/grokbuild/models", frozenset()),
    "worktree_create": (
        "POST",
        "/api/v1/grokbuild/worktrees",
        frozenset({"name", "branch", "source_repo", "create_branch", "start_point"}),
    ),
    "worktree_list": ("GET", "/api/v1/grokbuild/worktrees", frozenset()),
    "worktree_remove": (
        "DELETE",
        "/api/v1/grokbuild/worktrees/{name}",
        frozenset({"name"}),
    ),
    "push": (
        "POST",
        "/api/v1/grokbuild/worktrees/{name}/push",
        frozenset({"name", "remote", "branch", "set_upstream"}),
    ),
    "pr_create": (
        "POST",
        "/api/v1/grokbuild/worktrees/{name}/pull-requests",
        frozenset({"name", "pr_title", "pr_body", "pr_base", "pr_head", "draft"}),
    ),
    "fetch_result": (
        "GET",
        "/api/v1/grokbuild/dispatches/{dispatch_id}/result",
        frozenset({"dispatch_id", "format"}),
    ),
    "build": ("POST", "/api/v1/grokbuild/dispatches", _BUILD_PARAMS),
    "build_status": (
        "GET",
        "/api/v1/grokbuild/dispatches/{dispatch_id}",
        frozenset({"dispatch_id"}),
    ),
    "build_cancel": (
        "DELETE",
        "/api/v1/grokbuild/dispatches/{dispatch_id}",
        frozenset({"dispatch_id"}),
    ),
}

_PATH_PARAM_RE = re.compile(r"\{(\w+)\}")


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

    V2 additions (async build surface): ``build_status``, ``build_cancel``.
    ``op='build'`` now returns a 202 async envelope (``dispatch_id``,
    ``status_url``, ``events_url``); callers poll with ``build_status``
    and retrieve the result with ``fetch_result``.

    Retired in V1 (relay emits structured rejection):

    * ``op='dispatch'`` → ``retired_op`` (use ``op='build'``)
    * ``output_format='json'`` → ``retired_output_format`` (use ``streaming-json``)
    * ``continue_recent=True`` → ``retired_param`` (set ``session_id`` explicitly)

    Tier resolution (only relevant for ``op='build'``): the worker overlays
    preset values onto unspecified params; per-param explicit values always win.

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
    # Capture function params before any local variables are assigned.
    _kwargs = locals()

    if op == "dispatch":
        return _reject_local(
            "retired_op", "op='dispatch' was retired in V1; use op='build'"
        )
    if op not in _OPS:
        return _reject_local("unknown_op", f"unsupported op: {op!r}")

    method, path_template, allowed = _OPS[op]
    timeout = _BUILD_TIMEOUT if op == "build" else _SYNC_TIMEOUT
    op_params = {k: _kwargs[k] for k in allowed}
    return await _relay(method, path_template, op_params, timeout)


def _reject_local(reason_code: str, reason: str) -> dict[str, Any]:
    """Return a backwards-compatible rejected envelope for local relay rejections."""
    return {
        "dispatch_id": str(uuid.uuid4()),
        "status": "rejected",
        "stdout": "",
        "stderr": "",
        "exit_code": None,
        "duration_s": 0.0,
        "sidecar_path": None,
        "metadata": {"reason_code": reason_code, "reason": reason},
    }


async def _relay(
    method: str,
    path_template: str,
    params: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    path, body, query = _split_params(path_template, params, method)
    async with make_async_client(DEFAULT_STARGATE_URL, timeout=timeout) as client:
        try:
            resp = await client.request(method, path, json=body, params=query)
        except httpx.RequestError as exc:
            logger.error(
                "grokbuild relay transport failure: %s %s — %s", method, path, exc
            )
            return {
                "error": {"code": "grokbuild_worker_unreachable", "message": str(exc)}
            }

    if resp.status_code >= 400:
        return _http_error_to_mcp(resp)
    try:
        return resp.json()
    except ValueError:
        return {"error": {"code": "invalid_response", "message": resp.text[:200]}}


def _split_params(
    path_template: str,
    params: dict[str, Any],
    method: str,
) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
    """Resolve path params; split remainder into body (POST) or query (GET/DELETE)."""
    path_param_names: set[str] = set(_PATH_PARAM_RE.findall(path_template))
    path = _PATH_PARAM_RE.sub(
        lambda m: str(params.get(m.group(1), m.group(0))),
        path_template,
    )
    remaining = {
        k: v for k, v in params.items() if k not in path_param_names and v is not None
    }
    if method in ("GET", "DELETE"):
        return path, None, remaining or None
    return path, remaining or None, None


def _http_error_to_mcp(resp: httpx.Response) -> dict[str, Any]:
    """Map a worker HTTP error response back to the MCP error envelope shape."""
    try:
        body: dict[str, Any] = resp.json()
    except ValueError:
        body = {}
    raw_detail = body.get("detail")
    # Prefer the FastAPI HTTPException detail dict; fall back to the flat body
    # (e.g. TrackerCapacityError 429 returns reason_code/reason at the top level).
    src = raw_detail if isinstance(raw_detail, dict) else body
    reason_code = src.get(
        "reason_code", "relay_error" if resp.status_code < 500 else "op_failed"
    )
    reason = src.get("reason", f"HTTP {resp.status_code}")
    if resp.status_code < 500:
        meta: dict[str, Any] = {"reason_code": reason_code, "reason": reason}
        for k in ("running", "capacity", "retry_after"):
            if k in body:
                meta[k] = body[k]
        return {"status": "rejected", "metadata": meta}
    return {
        "status": "failed",
        "metadata": {"reason_code": reason_code, "reason": reason},
    }


def register_grokbuild_tools(mcp: FastMCP) -> None:
    """Mount grokbuild on the MCP catalog (decoration-at-register-time).

    Tool title is "Grok Build" (not "Grok Build Dispatch") — the V1 op set
    covers many flows (build, worktrees, fetch_result, push, pr_create);
    "Dispatch" was vestigial from when there was only the dispatch op.
    """
    mcp.tool(title="Grok Build")(grokbuild)

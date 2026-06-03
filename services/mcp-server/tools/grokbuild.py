"""grokbuild MCP tool — thin relay to grokbuild-worker via Stargate (V2).

**Harness status (2026-06):** grokbuild dispatch harness is *retired* for new
multi-writer work (assertion 11588 / 11622). Prefer ``cursorbuild`` for
close-to-code dispatches. This MCP surface remains registered as a vestigial
relay to ``grokbuild-worker`` for existing worktree/dispatch maintenance — not
for new arc workflows.

Routes every MCP op to the worker's ``/api/v1/grokbuild/*`` REST surface.
The MCP tool descriptor (caller-visible op vocabulary, parameter schemas) is
unchanged from V1 — only the execution host moved to grokbuild-worker.

Retired ops (``op='dispatch'``, unknown ops) are still rejected at the relay
layer to preserve backwards-compatible envelope shapes for callers that
pre-date the V2 cutover.

Observability playbook (full detail: cortex ``agent-skills/grokbuild.md``,
entity ``agent_skill:grokbuild``):

* ``op='build'`` returns 202 immediately; the relay does **not** poll.
* **While running:** ``op='build_status'`` (``state``, ``progress_summary``,
  ``last_event``, ``result_available``). Poll every 2–5s.
* **After terminal state:** ``op='fetch_result'`` (``format``: ``json`` |
  ``text`` | ``summary``) — stdout/stderr/audit fields live here only.
* **Cancel:** ``op='build_cancel'``.
* **Live grok output (richest):** NDJSON sidecar at
  ``$HOME/.local/share/grokbuild-worker/sidecars/{dispatch_id}.ndjson``
  (append-only; ``tail -f`` during run). Survives worker restarts; ~7d retention.
* **SSE lifecycle stream:** ``events_url`` from the 202 envelope → Stargate REST
  ``GET /api/v1/grokbuild/dispatches/{id}/events``. **Not** an MCP op.
  Coarse tracker events only; ``progress`` events are reserved/unused today.
* **Worker process log:** ``/tmp/logs/grokbuild-worker/grokbuild-worker.log``
  when started via manage — HTTP/startup errors, not grok subprocess stdout.
* **Event Service audit:** ``observability`` on ``grokbuild.dispatch.*`` (tracker)
  and ``mcp.grokbuild.dispatch.*`` (git diff, toolcalls); JOIN on ``dispatch_id``.
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
    "disable_web_search max_turns best_of_n resume_strict "
    "seat role recursion_depth mcp source_repo".split()
)

# (HTTP method, path template, allowed param names)
OPS: dict[str, tuple[str, str, frozenset[str]]] = {
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
    "snapshot": (
        "POST",
        "/api/v1/grokbuild/snapshots",
        frozenset({"source_repo", "slug", "name", "reset_main"}),
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
    # MQ3 (G7): audit + recursion enforcement.
    seat: str | None = None,  # caller seat slug; default "grok-api" applied at worker
    role: str | None = None,  # caller role slug; default "artisan" applied at worker
    recursion_depth: int | None = None,  # MQ3 depth tracking; worker rejects if > 2
    # MCP path selection (Phase D).
    mcp: bool = True,  # True → grok CLI subprocess w/ dispatch token; False → API direct
    # worktree ops surface.
    name: str = "",
    branch: str = "",
    source_repo: str = "",
    create_branch: bool = False,
    start_point: str = "",
    # snapshot op surface.
    slug: str = "",
    reset_main: bool = False,
    # fetch_result surface.
    dispatch_id: str = "",
    format: Literal["json", "text", "summary", "signals"] = "json",
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

    **RETIRED harness (11588):** do not use for new multi-writer work. Prefer
    ``cursorbuild`` (code dispatch) or ``frontier_dispatch`` (Grok consult).
    Overflow relay only — worker maintenance ops (worktree_*, build_status, etc.).

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
    * ``seat``: caller seat slug (e.g. ``grok-direct``); default applied at worker.
    * ``role``: caller role slug (e.g. ``artisan``); default applied at worker.
    * ``recursion_depth``: MQ3 dispatch chain depth. Worker rejects with
      ``recursion_depth_exceeded`` if > 2. Callers should pass the current
      depth from ``GROKBUILD_RECURSION_DEPTH`` (set in env by the outer worker).
    * ``mcp``: dispatch path selector (``op='build'`` only). ``True`` (default)
      spawns a grok CLI subprocess with the dispatch bearer token —
      ``grok-build-dispatch`` seat, full vortex surface available inside the
      dispatch. ``False`` calls the LLM API directly via Stargate (no subprocess,
      no MCP tooling inside the dispatch); use when the prompt is self-contained
      and the response is a text answer rather than a tool-driven task.

    Observability (``op='build'`` async dispatches only):

    The relay returns 202 and does **not** block until grok finishes. Callers
    MUST poll/stream/fetch explicitly. Full playbook:
    ``agent-skills/grokbuild.md`` (cortex) / entity ``agent_skill:grokbuild``.

    * **Poll:** ``build_status`` → ``state`` (``pending`` | ``running`` |
      ``succeeded`` | ``failed`` | ``cancelled``), ``progress_summary``,
      ``last_event``, ``result_available``, ``pid``, ``exit_code``.
    * **Result:** ``fetch_result`` after terminal state — canonical envelope
      (``stdout``, ``stderr``, ``git_diff_stat``, audit metadata). Formats:
      ``json`` (default), ``text``, ``summary``, ``signals`` (bounded:
      exit_code + stdout tail + failure lines — the fast path for gate checks).
      Every successful result also carries ``result_ref`` (fs-reachable spool
      pointer readable from any seat).
    * **Cancel:** ``build_cancel``.
    * **Live grok output:** sidecar NDJSON at
      ``~/.local/share/grokbuild-worker/sidecars/{dispatch_id}.ndjson``
      (``tail -f`` while running; readable without tracker after restart).
    * **SSE (REST only, not MCP):** ``events_url`` from the 202 response —
      ``GET {STARGATE}/api/v1/grokbuild/dispatches/{id}/events``. Tracker
      lifecycle events (``accepted``, terminal ``completed``); ``progress``
      events reserved/unused.
    * **Worker log:** ``/tmp/logs/grokbuild-worker/grokbuild-worker.log``
      (process-level when started via manage; not grok stdout).
    * **Event Service:** ``observability`` on ``grokbuild.dispatch.*`` and
      ``mcp.grokbuild.dispatch.*``; JOIN on ``dispatch_id`` for audit forensics.
    """
    # Capture function params before any local variables are assigned.
    _kwargs = locals()

    if op == "dispatch":
        return _reject_local(
            "retired_op", "op='dispatch' was retired in V1; use op='build'"
        )
    if op not in OPS:
        return _reject_local("unknown_op", f"unsupported op: {op!r}")

    method, path_template, allowed = OPS[op]
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
    """Mount grokbuild on the MCP catalog (vestigial relay — harness retired 11588).

    Demoted off Claude/Cursor primary manifest (``mcp_claude`` removed from
    canonical.yaml). Still registered for overflow ``dispatch(tool="grokbuild")``
    and grok flat manifest (``mcp_grok``). See ``INTENTIONAL_OVERFLOW`` in
    ``_coherence_allowlist.py``. Prefer cursorbuild / frontier_dispatch for new work.
    """
    mcp.tool(title="Grok Build")(grokbuild)

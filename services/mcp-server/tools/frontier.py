"""team_dispatch + frontier_dispatch MCP relays to Stargate.

Two tools, two contracts:

- ``team_dispatch(op=..., role=..., messages=..., dispatch_thread_id=..., ...)`` is the role-required
  door for team-seat consults. ``role`` selects a functional seat from the
  roster: lead / reviewer / gatherer / synthesizer / artisan / skeptic /
  investigator. Each resolves its own default (family, platform, model) via
  ``role:{slug}`` execution contract in Cortex. Legacy persona names (oppie →
  skeptic, forge → artisan, orion → gatherer, bard → synthesizer) are accepted
  via normalize_agent_slug alias table (Phase 7 of the agent-naming cleanup arc).
  Op enum: "generate" (returns content via tracker) or "to_thread"
  (dispatches with reply landing on ``thread``).
- ``frontier_dispatch(op=..., model=..., messages=..., ...)`` is direct frontier
  dispatch (no role envelope). Same op enum.

Both are thin async-by-default relays: forward to Stargate, return the dispatch
envelope (execution_id, pipeline, started_at, status) immediately.

Callers:
- For ``op="generate"``: poll with ``pipeline(op="result", execution_id=...)``
  to retrieve content.
- For ``op="to_thread"``: Stargate posts the model's reply on the
  role/model's behalf when the dispatch completes (architectural fix
  2026-05-22 — replaces the prior "observe the model self-posting"
  contract that failed for ``mcp=False`` and tool-budget-exhausted
  dispatches). Read with
  ``agent_bus(tool="fetch", arguments={"thread": ...})``. The tracker's
  terminal status reflects the on-behalf POST outcome.

Both tools share ``_relay`` for transport, JSON envelope handling, and error
normalization.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

import httpx
from mcp_events import record
from transport_utils import DEFAULT_STARGATE_URL, make_async_client
from universal_logging import get_logger

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = get_logger(__name__)

# Relay only handles admission (role contract + model admission at Stargate).
# Long-poll blocking is the caller's responsibility via pipeline(op="result").
_RELAY_TIMEOUT = 20.0


async def _relay(
    *,
    endpoint: str,
    body: dict[str, Any],
    record_prefix: str,
) -> dict[str, Any]:
    """Forward body to a Stargate endpoint and normalize the response envelope.

    Shared by ``team_dispatch`` and ``frontier_dispatch``.
    The ``record_prefix`` parameter routes telemetry rows to the right per-tool
    signal namespace (``mcp.team.dispatch.*`` vs ``mcp.frontier.dispatch.*``).
    """
    async with make_async_client(
        DEFAULT_STARGATE_URL, timeout=_RELAY_TIMEOUT
    ) as client:
        try:
            resp = await client.post(endpoint, json=body)
        except httpx.RequestError as exc:
            logger.error("%s relay transport failure: %s", record_prefix, exc)
            record(f"{record_prefix}.failed", error="transport")
            return {
                "error": {
                    "code": "stargate_unreachable",
                    "message": str(exc),
                }
            }

    try:
        payload = resp.json()
    except ValueError as exc:
        logger.error(
            "%s relay returned non-JSON response: status=%s error=%s",
            record_prefix,
            resp.status_code,
            exc,
        )
        record(
            f"{record_prefix}.failed",
            error="non_json",
            status=resp.status_code,
        )
        return {
            "error": {
                "code": f"http_{resp.status_code}",
                "message": resp.text[:500],
            }
        }

    # 5xx: upstream/infrastructure failure — bucket as `.failed`, not `.rejected`.
    # Stargate's `_error_response` issues 503s with `{"error": {...}}` JSON for
    # `pipeline_dispatch_unavailable` etc.; classifying those as `.rejected`
    # would attribute infra outages to caller misuse on dashboards.
    if resp.status_code >= 500:
        record(
            f"{record_prefix}.failed",
            error="upstream",
            status=resp.status_code,
        )
        return payload if isinstance(payload, dict) else {"error": payload}

    if resp.status_code >= 400:
        if isinstance(payload, dict) and "error" in payload:
            record(
                f"{record_prefix}.rejected",
                status=resp.status_code,
                field=payload.get("field") or "",
            )
            return payload
        detail_obj = payload.get("detail") if isinstance(payload, dict) else None
        # FastAPI's `extra="forbid"` 422 emits `{"detail": [{loc, msg, type, ...}]}` —
        # `detail` is a LIST. Normalize to the `FrontierEndpointError.to_dict()`
        # envelope shape so callers parse one error format and `.rejected`
        # carries the offending field name (last element of `loc`).
        if isinstance(detail_obj, list) and detail_obj:
            first = detail_obj[0] if isinstance(detail_obj[0], dict) else {}
            loc = first.get("loc") or []
            field = str(loc[-1]) if loc else ""
            msg = first.get("msg") or "validation error"
            record(
                f"{record_prefix}.rejected",
                status=resp.status_code,
                field=field,
            )
            return {
                "error": {"code": "validation_error", "message": msg},
                "field": field,
            }
        field = detail_obj.get("field", "") if isinstance(detail_obj, dict) else ""
        record(
            f"{record_prefix}.rejected",
            status=resp.status_code,
            field=field,
        )
        return detail_obj if isinstance(detail_obj, dict) else {"error": payload}

    record(
        f"{record_prefix}.dispatched",
        execution_id=payload.get("execution_id", "")
        if isinstance(payload, dict)
        else "",
    )
    return payload


def register_frontier_tools(mcp: FastMCP) -> None:
    """Register dispatch tools: team_dispatch and frontier_dispatch."""

    @mcp.tool(title="Team Dispatch")
    async def team_dispatch(
        op: Literal["generate", "to_thread"],
        role: str,
        messages: list[dict[str, Any]],
        dispatch_thread_id: str,
        model: str | None = None,
        system: str = "",
        reasoning_effort: str | None = None,
        generation_options: dict[str, Any] | None = None,
        max_tool_turns: int | None = None,
        transcript_id: str | None = None,
        caller_agent: str | None = None,
        timeout_seconds: int | None = None,
        thread: str | None = None,
        subject: str | None = None,
    ) -> dict[str, Any]:
        """Role-aware team-seat dispatch with explicit op discrimination.

        ``role`` selects a functional team seat from the roster (Phase 7 of the
        agent-naming cleanup arc). Available roles: lead, reviewer, gatherer,
        synthesizer, artisan, skeptic, investigator. Roles are model-agnostic:
        any explicit model may assume any role. Each role carries only a default
        (family, platform, model) used when ``model`` is omitted. Legacy persona
        names are accepted via the alias table (oppie → skeptic, forge →
        artisan, orion → gatherer, bard → synthesizer, api_claude → reviewer).

        Two ops:
        - ``op="generate"``: admits dispatch and returns ``{execution_id, ...}``.
          Poll with ``pipeline(op="result", execution_id=...)`` for content.
          ``thread`` / ``subject`` must be absent when using this op.
        - ``op="to_thread"``: admits dispatch; Stargate posts the role's
          reply to ``thread`` on its behalf after the dispatch completes
          (system-on-behalf delivery). Tracker terminal status reflects
          the POST outcome. ``thread`` is required. ``subject`` is
          optional (defaults to ``"{role} reply — execution {short_id}"``).

        Tool surface (no caller knob — derived from the effective model):
        - xAI multi-agent models — no client-side MCP tools.
        - Anthropic models — remote MCP when enabled by the dispatcher.
        - Other MCP-capable providers — in-process tool loop.

        Callers that need explicit no-role one-shot dispatch should use
        ``frontier_dispatch(mcp=False, ...)``.

        ``dispatch_thread_id`` — required compaction key for server-owned
        thread persistence on the ``team-dispatch`` pipeline. Prior turns are
        assembled from cortex; pass only the **latest** user message in
        ``messages``. Distinct from ``thread`` (agent-bus delivery on
        ``op="to_thread"``) and ``transcript_id`` (provenance only).

        ``transcript_id`` — caller's session ID for provenance attribution only.
        It is recorded in the execution record alongside ``caller_agent`` so
        dispatches can be traced back to the originating session. It is NOT
        forwarded to the dispatched role's context — the receiving model never
        sees it.
        """
        body: dict[str, Any] = {
            "op": op,
            "messages": messages,
            "role": role,
            "dispatch_thread_id": dispatch_thread_id,
            "system": system,
        }
        if op == "generate":
            if thread is not None or subject is not None:
                return {
                    "error": {
                        "code": "validation_error",
                        "message": "thread/subject are not allowed when op='generate'",
                    }
                }
        else:
            if thread is None:
                return {
                    "error": {
                        "code": "validation_error",
                        "message": "thread is required when op='to_thread'",
                    }
                }
            body["thread"] = thread
            if subject is not None:
                body["subject"] = subject

        for key, val in (
            ("model", model),
            ("reasoning_effort", reasoning_effort),
            ("generation_options", generation_options),
            ("max_tool_turns", max_tool_turns),
            ("transcript_id", transcript_id),
            ("caller_agent", caller_agent),
            ("timeout_seconds", timeout_seconds),
        ):
            if val is not None:
                body[key] = val

        record(
            "mcp.team.dispatch.called",
            role=role,
            op=op,
            model=model or "",
            reasoning_effort=reasoning_effort or "",
        )
        return await _relay(
            endpoint="/api/v1/team/dispatch",
            body=body,
            record_prefix="mcp.team.dispatch",
        )

    @mcp.tool(title="Frontier Dispatch")
    async def frontier_dispatch(
        op: Literal["generate", "to_thread"],
        model: str,
        messages: list[dict[str, Any]],
        system: str = "",
        mcp: bool = False,
        reasoning_effort: str | None = None,
        generation_options: dict[str, Any] | None = None,
        max_tool_turns: int | None = None,
        transcript_id: str | None = None,
        caller_agent: str | None = None,
        timeout_seconds: int | None = None,
        thread: str | None = None,
        subject: str | None = None,
        recursion_depth: int | None = None,
    ) -> dict[str, Any]:
        """Direct native-frontier dispatch (no role envelope) with explicit op discrimination.

        Two ops:
        - ``op="generate"``: admits dispatch and returns ``{execution_id, ...}``.
          Poll with ``pipeline(op="result", execution_id=...)`` for content.
          ``thread`` / ``subject`` must be absent.
        - ``op="to_thread"``: admits dispatch; Stargate posts the model's
          reply to ``thread`` on its behalf after the dispatch completes
          (system-on-behalf delivery — works with ``mcp=False``). ``thread``
          is required.

        ``mcp`` defaults to ``False`` (one-shot reasoning; no tool loop) — the
        canonical use of direct frontier dispatch is inline-substrate single-shot
        calls. Pass ``mcp=True`` to enable the full MCP catalog tool loop.
        Delivery on ``op="to_thread"`` is independent of ``mcp`` because the
        system posts the model's content on its behalf.

        ``transcript_id`` — caller's session ID for provenance attribution only.
        Recorded in the execution record; never forwarded to the dispatched model.
        A forward-reference to an in-progress session is fine.

        ``recursion_depth`` — MQ3 dispatch chain depth. Callers pass the current
        depth (read from ``GROKBUILD_RECURSION_DEPTH`` env or incremented from a
        parent dispatch). Stargate enforces depth ≤ 2; exceeding it returns
        ``reason_code="recursion_depth_exceeded"``.

        Use ``team_dispatch`` for role-envelope dispatch with team-seat assignment.
        """
        body: dict[str, Any] = {
            "op": op,
            "messages": messages,
            "model": model,
            "system": system,
            "mcp": mcp,
        }
        if op == "generate":
            if thread is not None or subject is not None:
                return {
                    "error": {
                        "code": "validation_error",
                        "message": "thread/subject are not allowed when op='generate'",
                    }
                }
        else:
            if thread is None:
                return {
                    "error": {
                        "code": "validation_error",
                        "message": "thread is required when op='to_thread'",
                    }
                }
            body["thread"] = thread
            if subject is not None:
                body["subject"] = subject

        for key, val in (
            ("reasoning_effort", reasoning_effort),
            ("generation_options", generation_options),
            ("max_tool_turns", max_tool_turns),
            ("transcript_id", transcript_id),
            ("caller_agent", caller_agent),
            ("timeout_seconds", timeout_seconds),
            ("recursion_depth", recursion_depth),
        ):
            if val is not None:
                body[key] = val

        record(
            "mcp.frontier.dispatch.called",
            op=op,
            model=model,
            reasoning_effort=reasoning_effort or "",
        )
        return await _relay(
            endpoint="/api/v1/frontier/dispatch",
            body=body,
            record_prefix="mcp.frontier.dispatch",
        )

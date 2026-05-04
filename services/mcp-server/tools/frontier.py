"""Team and frontier dispatch MCP relays to Stargate.

Four tools in two generations:

**Current generation (dispatch-surface-split Phase 1):**
- ``team_dispatch(op=..., agent=...)`` — persona-required, op-discriminated.
  ``op="generate"`` returns inline; ``op="to_thread"`` posts result to thread.
- ``frontier_dispatch(op=..., model=...)`` — persona-free, op-discriminated.

**Legacy (Phase 4 retirement pending):**
- ``team_generate(agent=...)`` — persona-required door, ``result_delivery``-based
  push. Active surface: 32 observed calls in last 15h. ¬remove until Phase 4.
- ``frontier_generate(...)`` — persona-free raw engine call. No observed traffic
  (signal absent from Event Service). Phase 4 rename is a near-no-op.

All four tools share ``_relay`` for transport, JSON envelope handling, and error
normalization. The split lives at the public-API boundary (tool registration):
no caller-facing flag toggles persona injection.
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

# Relay only handles admission (persona enforcement at Stargate + forward).
# Long-poll blocking is the caller's responsibility via pipeline(op="result").
_RELAY_TIMEOUT = 20.0


async def _relay(
    *,
    endpoint: str,
    body: dict[str, Any],
    record_prefix: str,
) -> dict[str, Any]:
    """Forward body to a Stargate endpoint and normalize the response envelope.

    Shared by both ``team_generate`` and ``frontier_generate`` registrations.
    The ``record_prefix`` parameter routes telemetry rows to the right per-tool
    signal namespace (``mcp.team.generate.*`` vs ``mcp.frontier.generate.*``).
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
    """Register dispatch tools: current-gen (team_dispatch, frontier_dispatch)
    and legacy (team_generate, frontier_generate — Phase 4 retirement pending)."""

    @mcp.tool(title="Team Generate")
    async def team_generate(
        agent: str,
        messages: list[dict[str, Any]],
        model: str | None = None,
        system: str = "",
        tools: list[str] | None = None,
        reasoning_effort: str | None = None,
        generation_options: dict[str, Any] | None = None,
        max_tool_turns: int | None = None,
        transcript_id: str | None = None,
        result_delivery: dict[str, Any] | None = None,
        caller_agent: str | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Persona-aware team-seat dispatch (async-by-default).

        Default door for any team consult (`oppie`, `orion`, `bard`,
        `api_claude`, `forge`). Stargate resolves persona contract and
        assembles birth prompt + briefing + continuation.

        Returns immediately with `{execution_id, pipeline, status, started_at}`.
        Poll with `pipeline(op="result", execution_id=...)` or use
        `result_delivery` for a pointer envelope push at terminal transition
        (posts execution metadata + poll URL to the bus thread — NOT model
        content; poll `pipeline(op="result")` after receiving the envelope
        to retrieve the actual output).

        For raw engine calls without persona assembly use `frontier_generate`.
        """
        body: dict[str, Any] = {
            "messages": messages,
            "agent": agent,
            "system": system,
        }
        if tools is not None:
            body["tools"] = tools
        for key, val in (
            ("model", model),
            ("reasoning_effort", reasoning_effort),
            ("generation_options", generation_options),
            ("max_tool_turns", max_tool_turns),
            ("transcript_id", transcript_id),
            ("result_delivery", result_delivery),
            ("caller_agent", caller_agent),
            ("timeout_seconds", timeout_seconds),
        ):
            if val is not None:
                body[key] = val

        record(
            "mcp.team.generate.called",
            agent=agent,
            model=model or "",
            reasoning_effort=reasoning_effort or "",
        )
        return await _relay(
            endpoint="/api/v1/team/generate",
            body=body,
            record_prefix="mcp.team.generate",
        )

    @mcp.tool(title="Frontier Generate")
    async def frontier_generate(
        messages: list[dict[str, Any]],
        model: str,
        system: str = "",
        reasoning_effort: str | None = None,
        generation_options: dict[str, Any] | None = None,
        max_tool_turns: int | None = None,
        transcript_id: str | None = None,
        result_delivery: dict[str, Any] | None = None,
        caller_agent: str | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Persona-free raw native-frontier dispatch (async-by-default).

        Direct engine call: no persona, no allowlists, no system-prompt
        assembly. Caller supplies their own ``system`` prompt or omits it for an
        empty system. For team-seat dispatches use ``team_generate`` instead.

        Returns immediately with ``{execution_id, pipeline, status, started_at}``.
        Poll with ``pipeline(op="result", execution_id=...)`` or use
        ``result_delivery`` for a pointer envelope push at terminal transition
        (posts execution metadata + poll URL — NOT model content; poll
        ``pipeline(op="result")`` after receiving the envelope to retrieve
        the actual output).
        """
        body: dict[str, Any] = {
            "messages": messages,
            "model": model,
            "system": system,
        }
        for key, val in (
            ("reasoning_effort", reasoning_effort),
            ("generation_options", generation_options),
            ("max_tool_turns", max_tool_turns),
            ("transcript_id", transcript_id),
            ("result_delivery", result_delivery),
            ("caller_agent", caller_agent),
            ("timeout_seconds", timeout_seconds),
        ):
            if val is not None:
                body[key] = val

        record(
            "mcp.frontier.generate.called",
            model=model,
            reasoning_effort=reasoning_effort or "",
        )
        return await _relay(
            endpoint="/api/v1/frontier/generate",
            body=body,
            record_prefix="mcp.frontier.generate",
        )

    # ---- dispatch-surface-split Phase 1: op-discriminated tools ----

    @mcp.tool(title="Team Dispatch")
    async def team_dispatch(
        op: Literal["generate", "to_thread"],
        agent: str,
        messages: list[dict[str, Any]],
        model: str | None = None,
        system: str = "",
        tools: list[str] | None = None,
        reasoning_effort: str | None = None,
        generation_options: dict[str, Any] | None = None,
        max_tool_turns: int | None = None,
        transcript_id: str | None = None,
        caller_agent: str | None = None,
        timeout_seconds: int | None = None,
        thread: str | None = None,
        subject: str | None = None,
    ) -> dict[str, Any]:
        """Persona-aware team-seat dispatch with explicit op discrimination.

        Two ops:
        - ``op="generate"``: admits dispatch and returns ``{execution_id, ...}``.
          Poll with ``pipeline(op="result", execution_id=...)`` for content.
          ``thread`` / ``subject`` must be absent when using this op.
        - ``op="to_thread"``: admits dispatch; the agent's reply lands on
          ``thread``; tracker terminal status reflects observed reply. ``thread``
          is required. ``subject`` is optional (auto-derived from last message
          if absent).

        Use ``frontier_dispatch`` for raw engine calls without a persona.

        Replaces ``team_generate`` (Phase 4 retirement pending).
        """
        body: dict[str, Any] = {
            "op": op,
            "messages": messages,
            "agent": agent,
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

        if tools is not None:
            body["tools"] = tools
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
            agent=agent,
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
        reasoning_effort: str | None = None,
        generation_options: dict[str, Any] | None = None,
        max_tool_turns: int | None = None,
        transcript_id: str | None = None,
        caller_agent: str | None = None,
        timeout_seconds: int | None = None,
        thread: str | None = None,
        subject: str | None = None,
    ) -> dict[str, Any]:
        """Persona-free raw native-frontier dispatch with explicit op discrimination.

        Two ops:
        - ``op="generate"``: admits dispatch and returns ``{execution_id, ...}``.
          Poll with ``pipeline(op="result", execution_id=...)`` for content.
          ``thread`` / ``subject`` must be absent.
        - ``op="to_thread"``: admits dispatch; model's reply lands on ``thread``.
          ``thread`` is required.

        Use ``team_dispatch`` for persona-aware dispatch with agent seat assignment.

        Replaces ``frontier_generate`` (Phase 4 retirement pending).
        """
        body: dict[str, Any] = {
            "op": op,
            "messages": messages,
            "model": model,
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

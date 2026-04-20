"""frontier_generate — thin public relay to /api/v1/frontier/generate.

Persona contract (allowed_models, tools, allowed_options) lives on
ai_agent:{slug} entities in cortex. The Stargate endpoint enforces the
contract generically — this MCP tool intentionally contains no persona
logic, no provider routing, no validation. Any persona behavior change
is a cortex edit (see scripts/cortex/sync-agent-identity.py), not a
code change.

Async-by-default: returns the dispatch envelope (execution_id, pipeline,
started_at, status) immediately. Callers poll with `pipeline(op="result",
execution_id=…)` or set `result_delivery` for push delivery to an
agent-bus thread on terminal transition.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import httpx
from mcp_events import record
from universal_logging import get_logger

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = get_logger(__name__)

_STARGATE_URL = os.environ.get("STARGATE_URL", "http://io:9999")

# Relay only handles admission (persona enforcement + forward). Long-poll
# blocking is the caller's responsibility via pipeline(op="result").
_RELAY_TIMEOUT = httpx.Timeout(connect=10.0, read=20.0, write=15.0, pool=10.0)


def register_frontier_tools(mcp: FastMCP) -> None:
    """Register public `frontier_generate`."""

    @mcp.tool(title="Frontier Generate")
    async def frontier_generate(
        messages: list[dict[str, Any]],
        agent: str | None = None,
        model: str | None = None,
        boot: str = "none",
        system: str = "",
        tools: list[str] | None = None,
        generation_options: dict[str, Any] | None = None,
        transcript_id: str | None = None,
        result_delivery: dict[str, Any] | None = None,
        caller_agent: str | None = None,
    ) -> dict[str, Any]:
        """Native-frontier dispatch via Stargate (async-by-default).

        Returns IMMEDIATELY with ``{execution_id, pipeline, status,
        started_at}``. To get the result:

        - **Poll**: ``pipeline(op="result", execution_id=<id>,
          wait_seconds=60.0)`` — server-side short-poll, clamped to 60s.
        - **Push**: pass ``result_delivery`` and Stargate posts the
          terminal envelope to an agent-bus thread when the pipeline
          completes — no polling required.

        Args:
          - ``agent``: optional persona slug (e.g. ``oppie``, ``orion``).
            When set, persona rules from cortex (default_model,
            allowed_models, tools, allowed_options) apply, and the
            agent's default model is used when ``model`` is omitted.
            **Requires** ``boot ∈ {mcp, team, full}`` — ``"none"`` is
            rejected when ``agent`` is set. Omit for a raw native call.
          - ``boot ∈ {none, mcp, team, full}``: persona hydration tier.
            ``"none"`` is only valid when ``agent`` is omitted.
          - ``tools``: explicit tool name list. Subset of persona's tools
            if ``agent`` set; subset of full tool registry otherwise.
            Omit for the persona's default toolset.
          - ``generation_options``: max_tokens, temperature,
            reasoning_effort, thinking, etc. Adapter handles per-provider
            shaping.
          - ``result_delivery``: ``{bus_thread, bus_from_agent,
            bus_to_agent, bus_subject}`` — Stargate posts the terminal
            envelope to the configured agent-bus thread when the pipeline
            completes. Agents do NOT receive this automatically — they
            only read bus messages when instructed to. Practical uses:
            (a) automated scripts / pipeline steps monitoring the bus,
            (b) the same session explicitly fetching the thread later,
            (c) multi-agent chains where a coordinator instructs the next
            agent to read the thread after the dispatch settles.
          - ``caller_agent``: dispatch provenance string stored on the
            tracker record and emitted on dispatch events.

        For interactive agent use, the canonical completion pattern is:
        dispatch → save ``execution_id`` → do other work → call
        ``pipeline(op="result", execution_id=…, wait_seconds=60)``
        when ready, re-polling until status is terminal.

        On persona violation: returns error envelope
        ``{error: {code, message}, field, request_id}`` with no
        execution_id — admission is refused before tracker entry.
        """
        if agent is not None and boot == "none":
            return {
                "error": {
                    "code": "invalid_request",
                    "message": "boot must not be 'none' when agent is set — specify 'mcp', 'team', or 'full'",
                },
                "field": "boot",
            }

        body: dict[str, Any] = {
            "messages": messages,
            "boot": boot,
            "system": system,
        }
        for key, val in (
            ("agent", agent),
            ("model", model),
            ("tools", tools),
            ("generation_options", generation_options),
            ("transcript_id", transcript_id),
            ("result_delivery", result_delivery),
            ("caller_agent", caller_agent),
        ):
            if val is not None:
                body[key] = val

        record(
            "mcp.frontier.generate.called",
            agent=agent or "",
            model=model or "",
            boot=boot,
        )
        async with httpx.AsyncClient(
            base_url=_STARGATE_URL, timeout=_RELAY_TIMEOUT
        ) as client:
            try:
                resp = await client.post("/api/v1/frontier/generate", json=body)
            except httpx.RequestError as exc:
                logger.error("frontier_generate relay transport failure: %s", exc)
                record("mcp.frontier.generate.failed", error="transport")
                return {
                    "error": {
                        "code": "stargate_unreachable",
                        "message": str(exc),
                    }
                }

        try:
            payload = resp.json()
        except ValueError:
            return {
                "error": {
                    "code": f"http_{resp.status_code}",
                    "message": resp.text[:500],
                }
            }
        if resp.status_code >= 400:
            detail_obj = payload.get("detail") if isinstance(payload, dict) else None
            field = detail_obj.get("field", "") if isinstance(detail_obj, dict) else ""
            record(
                "mcp.frontier.generate.rejected",
                status=resp.status_code,
                field=field,
            )
            return detail_obj if isinstance(detail_obj, dict) else {"error": payload}
        record(
            "mcp.frontier.generate.dispatched",
            execution_id=payload.get("execution_id", "")
            if isinstance(payload, dict)
            else "",
        )
        return payload

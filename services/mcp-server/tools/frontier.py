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

from typing import TYPE_CHECKING, Any

import httpx
from mcp_events import record
from transport_utils import DEFAULT_STARGATE_URL, make_async_client
from universal_logging import get_logger

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = get_logger(__name__)

# Relay only handles admission (persona enforcement + forward). Long-poll
# blocking is the caller's responsibility via pipeline(op="result").
_RELAY_TIMEOUT = 20.0


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
        reasoning_effort: str | None = None,
        generation_options: dict[str, Any] | None = None,
        max_tool_turns: int | None = None,
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
            **Name collision warning**: pass ``"brave_search"`` (not
            ``"web_search"``) for live Brave Search results.
            ``"web_search"`` collides with Claude's and Gemini's native
            search tool name — the model may silently call its own
            capability instead of the Brave API, returning stale
            training-data knowledge. ``"brave_search"`` is the safe
            alias; the executor remaps it to the MCP ``web_search`` call.
          - ``reasoning_effort`` ∈ ``{"low", "medium", "high"}``:
            convenience knob. Translated to the provider-native thinking
            config automatically (Anthropic: ``thinking.budget_tokens``;
            OpenAI/xAI: ``reasoning.effort``; Google: ``thinkingBudget``
            / ``thinkingLevel``). Only applied if ``generation_options``
            does not set ``thinking`` explicitly.
          - ``generation_options``: pass-through generation params
            forwarded untranslated (``temperature``, ``max_tokens``,
            ``top_p``, ``top_k``, ``stop``, ``seed``,
            ``response_format``, ``tool_choice``, ``thinking``). For
            full per-provider control, pass ``thinking`` directly using
            the vendor's native shape; for params not surfaced here,
            dispatch via ``pipeline(pipeline_id="frontier-dispatch",
            pipeline_options={"generation_parameters": {...}})``.
          - ``max_tool_turns``: maximum number of tool-call/response
            cycles the dispatch loop will execute before terminating.
            Defaults to 10 when omitted. Raise to 50–100 for
            investigation-grade tasks (diff review, architecture
            analysis) where the model reads many files before producing
            output.
          - ``result_delivery``: ``{bus_thread, bus_from_agent,
            bus_to_agent, bus_subject, bus_brief_summary, bus_attachments}``
            — Stargate posts a compact pointer envelope to the configured
            agent-bus thread when the pipeline completes.  The body contains
            execution_id, status, usage, duration_s, and a poll pointer —
            full model output is never inlined (use the poll endpoint to
            fetch it).  Optional ``bus_brief_summary`` adds a caller-supplied
            text summary to the envelope; ``bus_attachments`` forwards a list
            of attachment dicts (filename, path, …) to the bus turn.  Agents
            do NOT receive this automatically — they only read bus messages
            when instructed to. Practical uses:
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
        body: dict[str, Any] = {
            "messages": messages,
            "boot": boot,
            "system": system,
        }
        for key, val in (
            ("agent", agent),
            ("model", model),
            ("tools", tools),
            ("reasoning_effort", reasoning_effort),
            ("generation_options", generation_options),
            ("max_tool_turns", max_tool_turns),
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
        async with make_async_client(
            DEFAULT_STARGATE_URL, timeout=_RELAY_TIMEOUT
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
        except ValueError as exc:
            logger.error(
                "frontier_generate relay returned non-JSON response: status=%s error=%s",
                resp.status_code,
                exc,
            )
            return {
                "error": {
                    "code": f"http_{resp.status_code}",
                    "message": resp.text[:500],
                }
            }
        if resp.status_code >= 400:
            if isinstance(payload, dict) and "error" in payload:
                # Top-level error envelope — FrontierEndpointError shape
                # {error, field, request_id} or pipeline dispatch error
                # {error: {code, message}}.  Pass through directly so
                # callers receive the structured field/request_id contract.
                record(
                    "mcp.frontier.generate.rejected",
                    status=resp.status_code,
                    field=payload.get("field") or "",
                )
                return payload
            # FastAPI 422 wraps validation errors under "detail"
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

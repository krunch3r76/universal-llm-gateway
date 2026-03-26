"""MCP tool — multi-model advisory consultation via native function calling.

Dispatches queries to external LLM providers (Grok, GPT-4o) with read-only
Cortex + RAG tool access. Each provider runs an independent agent loop,
gathering evidence via tools before synthesizing a response.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_agent_consult_tools(mcp: FastMCP) -> None:
    """Register the agent_consult tool on the MCP server instance."""

    @mcp.tool()
    def agent_consult(
        query: str,
        providers: list[str] | None = None,
        context_entities: list[str] | None = None,
    ) -> dict[str, Any]:
        """Multi-model advisory consultation with Cortex + RAG tool access.

        Sends a query to external LLM providers (Grok via xAI, GPT-4o via
        OpenAI) with native function calling. Each provider independently
        queries Cortex entities/assertions and RAG documents to build an
        evidence-based response. Read-only — external models cannot write
        to Cortex.

        **When to use**: Complex questions benefiting from multiple
        independent reasoning paths — financial analysis, legal strategy,
        tax planning, evidence triangulation. The value is that different
        models catch different things over the same structured data.

        **When NOT to use**: Simple factual lookups (use cortex/rag
        directly), routine coding tasks, or when speed matters more than
        depth (each provider loop takes 10-60s).

        Args:
            query: The advisory question or analysis request.
            providers: Which providers to consult. Default: all with
                configured API keys. Options: "grok", "openai".
            context_entities: Entity IDs to pre-load into context before
                the query (e.g. ["person:kaywan-mansubi",
                "legal_matter:osaic-demand"]). Optional — models can also
                discover entities via tools.

        Returns:
            Per-provider results with content, tool call counts, and
            timing. Each provider entry has: content (the response),
            model, tool_calls_made, turns, duration_s, usage.
        """
        from ._agent_loop import run_consult

        return run_consult(query, providers, context_entities)

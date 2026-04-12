"""Frontier generation tools — provider-specific MCP surfaces.

``grok_generate`` and ``claude_generate`` are the primary tools with
provider-aware signatures and defaults.  ``frontier_generate`` stays as a
backward-compatible dispatch that routes to the correct provider.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._frontier_core import (
    OPENAI_SERVER_TOOL_MAP,
    XAI_SERVER_TOOL_MAP,
    build_frontier_request,
    execute_frontier,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

_GROK_OPPIE_BOOT_REFS: dict[str, str] = {
    "mcp": "notes/system/prompts/oppie-seed-mcp-v1.5.md",
    "team": "notes/system/prompts/oppie-seed-mcp-v1.5.md",
    "full": "notes/system/prompts/oppie-seed-full-v1.5.md",
}
# Models that auto-load the Oppie persona seed when boot != "none".
_GROK_OPPIE_MODELS: set[str] = {"grok-4.20-multi-agent-0309"}

_CLAUDE_BOOT_REF_DEFAULTS: dict[str, str] = {
    "mcp": "notes/system/prompts/api-claude-seed-v1.0.md",
    "team": "notes/system/prompts/api-claude-seed-v1.0.md",
    "full": "notes/system/prompts/claude-seed-full-v1.0.md",
}

_OPENAI_BOOT_REF_DEFAULTS: dict[str, str] = {
    "mcp": "notes/system/prompts/api-claude-seed-v1.0.md",
}


def register_frontier_tools(mcp: FastMCP) -> None:
    """Register grok_generate, claude_generate, and frontier_generate (compat)."""

    @mcp.tool(title="Grok Generate")
    def grok_generate(
        messages: list[dict[str, Any]],
        model: str = "grok-4.20-0309-reasoning",
        system: str = "",
        max_output_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        stop_sequences: list[str] | None = None,
        seed: int | None = None,
        response_format: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
        include_encrypted_reasoning: bool = False,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        server_tools: list[str] | None = None,
        conversation_id: str | None = None,
        reasoning_trace: list[dict[str, Any]] | None = None,
        boot: str = "mcp",
        boot_ref: str | None = None,
        include_raw: bool = False,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Generate with xAI Grok models via Stargate provider-native endpoint.

        **Boot and tools** (two orthogonal axes):

        - ``boot`` controls system prompt / persona:
          - ``"mcp"`` (default) — loads API subagent seed; Cortex/RAG read
            tools injected (cortex_entity_get, cortex_search_entities,
            cortex_assertions, cortex_deadlines, rag_search).
          - ``"none"`` — no system prompt, no tool definitions.
          - ``"team"`` / ``"full"`` — Oppie birth prompt (identity) +
            operational context + **full tool surface**: all ``"mcp"``
            tools plus ``cortex`` (unified dispatch — assert, observe,
            supersede, journal_write, edge_create, search, and more)
            and ``agent_bus`` (fetch, reply, post threads).
            ``"full"`` also adds Cortex boot narrative.
        - For standard models (boot != "none"): client-side tool definitions
          are injected and the tool loop runs locally.
        - For ``grok-4.20-multi-agent-*`` (boot != "none"): a remote MCP entry
          pointing at ``https://mcp.k-1.me/mcp`` is injected instead — xAI
          calls the MCP server directly and manages the tool loop server-side.

        **Model selection**:

        - ``grok-4.20-0309-reasoning`` — top model, built-in reasoning, tool
          calling, 2M ctx. **DEFAULT** — use for all Oppie-style subagent calls.
        - ``grok-4.20-0309-non-reasoning`` — same without reasoning overhead.
        - ``grok-4.20-multi-agent-0309`` — multi-agent orchestration; full MCP
          tool surface via remote MCP (xAI server-side loop).
        - ``grok-4-1-fast-reasoning`` — fast + cheap reasoning.
        - ``grok-4-1-fast-non-reasoning`` — fast without reasoning.
        - ``grok-3-mini`` — legacy; supports reasoning_effort
          ("low"/"medium"/"high", silently stripped for grok-4 models).

        **Unique capabilities**:
        - ``x_search`` server tool — real-time X/Twitter signal retrieval
          ($5/1k calls). No equivalent on other providers.
        - ``conversation_id`` — multi-turn stateful conversations server-side.
        - ``reasoning_trace`` — inject prior reasoning context into follow-up
          requests for continued chain-of-thought.
        - 2M token context window on grok-4.20 family.
        """
        full_model = model if "/" in model else f"xai/{model}"

        if boot_ref is None:
            base_model = model.split("/")[-1] if "/" in model else model
            if base_model in _GROK_OPPIE_MODELS:
                boot_ref = _GROK_OPPIE_BOOT_REFS.get(boot)

        thinking: dict[str, Any] | None = None
        if reasoning_effort or include_encrypted_reasoning:
            thinking = {}
            if reasoning_effort:
                thinking["effort"] = reasoning_effort
            if include_encrypted_reasoning:
                thinking["include_encrypted"] = True

        all_tools: list[dict[str, Any]] = []
        if server_tools:
            for st in server_tools:
                mapped = XAI_SERVER_TOOL_MAP.get(st)
                if mapped:
                    all_tools.append(dict(mapped))
        if tools:
            all_tools.extend(tools)

        req = build_frontier_request(
            model=full_model,
            messages=messages,
            system=system,
            boot=boot,
            boot_ref=boot_ref,
            agent="oppie",
            max_tokens=max_output_tokens,
            temperature=temperature,
            top_p=top_p,
            stop_sequences=stop_sequences,
            seed=seed,
            thinking=thinking,
            tools=all_tools or None,
            tool_choice=tool_choice,
            response_format=response_format,
            conversation_id=conversation_id,
            reasoning_trace=reasoning_trace,
        )
        if isinstance(req, dict):
            return req
        return execute_frontier(
            model=full_model,
            req=req,
            include_raw=include_raw,
            tool_name="grok_generate",
            timeout=timeout,
        )

    @mcp.tool(title="Claude Generate")
    def claude_generate(
        messages: list[dict[str, Any]],
        model: str = "claude-sonnet-4-6",
        system: str = "",
        max_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        stop_sequences: list[str] | None = None,
        response_format: dict[str, Any] | None = None,
        thinking: str | dict[str, Any] | None = None,
        effort: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        server_tools: list[str] | None = None,
        speed: str | None = None,
        provider_options: dict[str, Any] | None = None,
        boot: str = "mcp",
        boot_ref: str | None = None,
        include_raw: bool = False,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Generate with Anthropic Claude models via Stargate provider-native endpoint.

        Default boot=mcp loads the API Claude seed — an MCP analytical operator
        identity (subordinate to caller, tool-disciplined, journal-capable).
        This is distinct from Web Claude (strategic advisor) and Cursor Claude.

        **Boot levels and tool surface**:

        - ``"mcp"`` (default) — subagent seed + Cortex/RAG read tools
          (cortex_entity_get, cortex_search_entities, cortex_assertions,
          cortex_deadlines, rag_search).
        - ``"none"`` — no system prompt, no tool definitions.
        - ``"team"`` / ``"full"`` — API Claude birth prompt + all ``"mcp"``
          tools plus ``cortex`` (unified dispatch — assert, observe,
          supersede, journal_write, edge_create, search, and more) and
          ``agent_bus`` (fetch, reply, post threads).
          ``"full"`` also adds Cortex boot narrative.

        ``timeout`` overrides read timeout in seconds (default 600, max 1800).

        **Unique capabilities**:
        - Extended thinking with ``budget_tokens`` on claude-opus-4 — deep
          multi-step reasoning with explicit token allocation.
        - ``speed="fast"`` on claude-sonnet-4 — reduced latency tier.
        - Document blocks — native PDF input via base64 in message content
          (bypasses local pymupdf4llm extraction for higher fidelity).
        - Citations — structured source attribution in responses.
        - Computer use beta — GUI interaction capability.

        Models:
          claude-sonnet-4    — fast + capable, 16k output, adaptive thinking (DEFAULT)
          claude-opus-4      — top model, 32k output, extended thinking
          claude-3-5-sonnet  — previous gen, 8k output
        """
        full_model = model if "/" in model else f"anthropic/{model}"

        if boot_ref is None:
            boot_ref = _CLAUDE_BOOT_REF_DEFAULTS.get(boot)

        thinking_dict: dict[str, Any] | None = None
        if isinstance(thinking, str):
            if thinking == "adaptive":
                thinking_dict = {"type": "adaptive"}
            elif thinking == "disabled":
                thinking_dict = {"type": "disabled"}
        elif isinstance(thinking, dict):
            thinking_dict = thinking

        all_tools: list[dict[str, Any]] = []
        if server_tools:
            for st in server_tools:
                all_tools.append({"type": st})
        if tools:
            all_tools.extend(tools)

        opts = dict(provider_options or {})
        if speed == "fast":
            anthropic_opts = dict(opts.get("anthropic", {}))
            anthropic_opts["speed"] = "fast"
            opts["anthropic"] = anthropic_opts

        req = build_frontier_request(
            model=full_model,
            messages=messages,
            system=system,
            boot=boot,
            boot_ref=boot_ref,
            agent="api_claude",
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            stop_sequences=stop_sequences,
            thinking=thinking_dict,
            effort=effort,
            tools=all_tools or None,
            tool_choice=tool_choice,
            response_format=response_format,
            provider_options=opts or None,
        )
        if isinstance(req, dict):
            return req
        return execute_frontier(
            model=full_model,
            req=req,
            include_raw=include_raw,
            tool_name="claude_generate",
            timeout=timeout,
        )

    @mcp.tool(title="OpenAI Generate")
    def openai_generate(
        messages: list[dict[str, Any]],
        model: str = "gpt-5.4",
        system: str = "",
        max_output_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        stop_sequences: list[str] | None = None,
        seed: int | None = None,
        response_format: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        server_tools: list[str] | None = None,
        provider_options: dict[str, Any] | None = None,
        boot: str = "mcp",
        boot_ref: str | None = None,
        include_raw: bool = False,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Generate with OpenAI models via Stargate provider-native Responses API.

        Routes through ``/api/v1/providers/openai/responses`` on Stargate.

        **NOT for search models** — ``gpt-5-search-api``, ``gpt-4o-search-preview``,
        and any ``*-search-*`` / ``*-search-api`` variant are Chat Completions-only.
        They are unavailable on the Responses API and reject custom tool definitions.
        Use ``llm_generate(model="openai/gpt-5-search-api", ...)`` for those models.

        **Boot levels and tool surface**:

        - ``"mcp"`` (default) — subagent seed + Cortex/RAG read tools
          (cortex_entity_get, cortex_search_entities, cortex_assertions,
          cortex_deadlines, rag_search). Tool loop runs client-side automatically.
        - ``"none"`` — no system prompt, no tool definitions.
        - ``"team"`` / ``"full"`` — Orion birth prompt (identity) +
          all ``"mcp"`` tools plus ``cortex`` (unified dispatch — assert,
          observe, supersede, journal_write, edge_create, search, and
          more) and ``agent_bus`` (fetch, reply, post threads).
          ``"full"`` also adds Cortex boot narrative.

        Models:
          gpt-5.4              — best intelligence, agentic + coding (DEFAULT)
          gpt-5.4-mini         — strong mini for coding, computer use, subagents
          gpt-5.4-nano         — cheapest GPT-5.4 class, high-volume tasks
          o4-mini              — reasoning model, deductive tasks
          o3                   — deep reasoning
        """
        full_model = model if "/" in model else f"openai/{model}"

        if boot_ref is None:
            boot_ref = _OPENAI_BOOT_REF_DEFAULTS.get(boot)

        thinking: dict[str, Any] | None = None
        if reasoning_effort:
            thinking = {"effort": reasoning_effort}

        all_tools: list[dict[str, Any]] = []
        if server_tools:
            for st in server_tools:
                mapped = OPENAI_SERVER_TOOL_MAP.get(st)
                if mapped:
                    all_tools.append(dict(mapped))
        if tools:
            all_tools.extend(tools)

        req = build_frontier_request(
            model=full_model,
            messages=messages,
            system=system,
            boot=boot,
            boot_ref=boot_ref,
            agent="orion",
            max_tokens=max_output_tokens,
            temperature=temperature,
            top_p=top_p,
            stop_sequences=stop_sequences,
            seed=seed,
            thinking=thinking,
            tools=all_tools or None,
            tool_choice=tool_choice,
            response_format=response_format,
            provider_options=provider_options,
        )
        if isinstance(req, dict):
            return req
        return execute_frontier(
            model=full_model,
            req=req,
            include_raw=include_raw,
            tool_name="openai_generate",
            timeout=timeout,
        )

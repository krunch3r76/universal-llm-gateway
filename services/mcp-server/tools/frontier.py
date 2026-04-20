"""Frontier generation tool — unified ``frontier_generate(frontier, …)`` primary surface.

Single primary MCP tool dispatching to per-provider routers. Semantic
selector ``frontier ∈ {"grok","claude","openai","gemini"}`` picks the
internal strategy; the shared schema exposes common generation params
plus a per-provider ``extra`` escape hatch.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal

from ._frontier_core import (
    GOOGLE_SERVER_TOOL_MAP,
    OPENAI_SERVER_TOOL_MAP,
    XAI_SERVER_TOOL_MAP,
    build_frontier_request,
    execute_frontier,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

BootLevel = Literal["none", "mcp", "team", "full"]
FrontierKind = Literal["grok", "claude", "openai", "gemini"]
ReasoningEffort = Literal["minimal", "low", "medium", "high", "max"]

_FRONTIER_DEFAULTS: dict[FrontierKind, str] = {
    "grok": "grok-4.20-0309-reasoning",
    "claude": "claude-sonnet-4-6",
    "openai": "gpt-5.4",
    "gemini": "gemini-2.5-flash",
}

_FRONTIER_AGENT: dict[FrontierKind, str] = {
    "grok": "oppie",
    "claude": "api_claude",
    "openai": "orion",
    "gemini": "bard",
}

_GROK_OPPIE_BOOT_REFS: dict[str, str] = {
    "mcp": "notes/system/prompts/oppie-seed-mcp-v1.5.md",
    "team": "notes/system/prompts/oppie-seed-mcp-v1.5.md",
    "full": "notes/system/prompts/oppie-seed-full-v1.5.md",
}
_GROK_OPPIE_MODELS: set[str] = {"grok-4.20-multi-agent-0309"}

_CLAUDE_BOOT_REF_DEFAULTS: dict[str, str] = {
    "mcp": "notes/system/prompts/api-claude-seed-v1.0.md",
    "team": "notes/system/prompts/api-claude-seed-v1.0.md",
    "full": "notes/system/prompts/claude-seed-full-v1.0.md",
}

_OPENAI_BOOT_REF_DEFAULTS: dict[str, str] = {
    "mcp": "notes/system/prompts/api-claude-seed-v1.0.md",
}

_GEMINI_BOOT_REF_DEFAULTS: dict[str, str] = {
    "mcp": "agent-identity/bard-birth.md",
    "team": "agent-identity/bard-birth.md",
    "full": "agent-identity/bard-birth.md",
}


def _frontier_grok(
    *,
    messages: list[dict[str, Any]],
    model: str,
    system: str,
    max_tokens: int | None,
    temperature: float | None,
    top_p: float | None,
    stop_sequences: list[str] | None,
    seed: int | None,
    response_format: dict[str, Any] | None,
    reasoning_effort: ReasoningEffort | None,
    thinking: Literal["adaptive", "disabled"] | dict[str, Any] | None,
    tools: list[dict[str, Any]] | None,
    tool_choice: str | dict[str, Any] | None,
    server_tools: list[str] | None,
    boot: BootLevel,
    boot_ref: str | None,
    remote_mcp: bool,
    extra: dict[str, Any],
) -> dict[str, Any] | tuple[str, Any]:
    del thinking
    # Client-side tool injection (via libs/agent_seat/native_loop) is the default
    # for every boot level; remote_mcp is opt-in only. The team/full → multi-agent
    # model remap ALSO fires only on explicit opt-in, because multi-agent-0309
    # structurally refuses client-side tools and only works through xAI's remote
    # MCP server — it must not be selected implicitly.
    use_remote_mcp = remote_mcp
    if use_remote_mcp and model == _FRONTIER_DEFAULTS["grok"]:
        model = "grok-4.20-multi-agent-0309"
    full_model = model if "/" in model else f"xai/{model}"

    if boot_ref is None:
        base_model = model.split("/")[-1] if "/" in model else model
        if base_model in _GROK_OPPIE_MODELS:
            boot_ref = _GROK_OPPIE_BOOT_REFS.get(boot)

    effort = reasoning_effort if reasoning_effort in ("low", "medium", "high") else None
    include_encrypted = bool(extra.get("include_encrypted_reasoning"))
    thinking_dict: dict[str, Any] | None = None
    if effort or include_encrypted:
        thinking_dict = {}
        if effort:
            thinking_dict["effort"] = effort
        if include_encrypted:
            thinking_dict["include_encrypted"] = True

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
        agent=_FRONTIER_AGENT["grok"],
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        stop_sequences=stop_sequences,
        seed=seed,
        thinking=thinking_dict,
        tools=all_tools or None,
        tool_choice=tool_choice,
        response_format=response_format,
        conversation_id=extra.get("conversation_id"),
        reasoning_trace=extra.get("reasoning_trace"),
        remote_mcp=use_remote_mcp,
    )
    if isinstance(req, dict):
        return req
    return (full_model, req)


def _frontier_claude(
    *,
    messages: list[dict[str, Any]],
    model: str,
    system: str,
    max_tokens: int | None,
    temperature: float | None,
    top_p: float | None,
    stop_sequences: list[str] | None,
    seed: int | None,
    response_format: dict[str, Any] | None,
    reasoning_effort: ReasoningEffort | None,
    thinking: Literal["adaptive", "disabled"] | dict[str, Any] | None,
    tools: list[dict[str, Any]] | None,
    tool_choice: str | dict[str, Any] | None,
    server_tools: list[str] | None,
    boot: BootLevel,
    boot_ref: str | None,
    remote_mcp: bool,
    extra: dict[str, Any],
) -> dict[str, Any] | tuple[str, Any]:
    del seed
    full_model = model if "/" in model else f"anthropic/{model}"
    # Client-side tool injection is the default; remote_mcp is opt-in only.
    use_remote_mcp = remote_mcp

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

    claude_effort = (
        reasoning_effort
        if reasoning_effort in ("low", "medium", "high", "max")
        else None
    )

    all_tools: list[dict[str, Any]] = []
    if server_tools:
        for st in server_tools:
            all_tools.append({"type": st})
    if tools:
        all_tools.extend(tools)

    opts = dict(extra.get("provider_options") or {})
    if extra.get("speed") == "fast":
        anthropic_opts = dict(opts.get("anthropic", {}))
        anthropic_opts["speed"] = "fast"
        opts["anthropic"] = anthropic_opts

    req = build_frontier_request(
        model=full_model,
        messages=messages,
        system=system,
        boot=boot,
        boot_ref=boot_ref,
        agent=_FRONTIER_AGENT["claude"],
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        stop_sequences=stop_sequences,
        thinking=thinking_dict,
        effort=claude_effort,
        tools=all_tools or None,
        tool_choice=tool_choice,
        response_format=response_format,
        provider_options=opts or None,
        remote_mcp=use_remote_mcp,
    )
    if isinstance(req, dict):
        return req
    return (full_model, req)


def _frontier_openai(
    *,
    messages: list[dict[str, Any]],
    model: str,
    system: str,
    max_tokens: int | None,
    temperature: float | None,
    top_p: float | None,
    stop_sequences: list[str] | None,
    seed: int | None,
    response_format: dict[str, Any] | None,
    reasoning_effort: ReasoningEffort | None,
    thinking: Literal["adaptive", "disabled"] | dict[str, Any] | None,
    tools: list[dict[str, Any]] | None,
    tool_choice: str | dict[str, Any] | None,
    server_tools: list[str] | None,
    boot: BootLevel,
    boot_ref: str | None,
    remote_mcp: bool,
    extra: dict[str, Any],
) -> dict[str, Any] | tuple[str, Any]:
    del thinking
    full_model = model if "/" in model else f"openai/{model}"

    if boot_ref is None:
        boot_ref = _OPENAI_BOOT_REF_DEFAULTS.get(boot)

    openai_effort = (
        reasoning_effort
        if reasoning_effort in ("minimal", "low", "medium", "high")
        else None
    )
    thinking_dict: dict[str, Any] | None = None
    if openai_effort:
        thinking_dict = {"effort": openai_effort}

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
        agent=_FRONTIER_AGENT["openai"],
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        stop_sequences=stop_sequences,
        seed=seed,
        thinking=thinking_dict,
        tools=all_tools or None,
        tool_choice=tool_choice,
        response_format=response_format,
        provider_options=extra.get("provider_options"),
        remote_mcp=remote_mcp,
    )
    if isinstance(req, dict):
        return req
    return (full_model, req)


def _frontier_gemini(
    *,
    messages: list[dict[str, Any]],
    model: str,
    system: str,
    max_tokens: int | None,
    temperature: float | None,
    top_p: float | None,
    stop_sequences: list[str] | None,
    seed: int | None,
    response_format: dict[str, Any] | None,
    reasoning_effort: ReasoningEffort | None,
    thinking: Literal["adaptive", "disabled"] | dict[str, Any] | None,
    tools: list[dict[str, Any]] | None,
    tool_choice: str | dict[str, Any] | None,
    server_tools: list[str] | None,
    boot: BootLevel,
    boot_ref: str | None,
    remote_mcp: bool,
    extra: dict[str, Any],
) -> dict[str, Any] | tuple[str, Any]:
    del thinking
    full_model = model if "/" in model else f"google/{model}"

    if boot_ref is None:
        boot_ref = _GEMINI_BOOT_REF_DEFAULTS.get(boot)

    gemini_level: str | None = None
    if reasoning_effort in ("low", "medium", "high"):
        gemini_level = reasoning_effort.upper()
    thinking_dict: dict[str, Any] | None = None
    if gemini_level:
        thinking_dict = {"level": gemini_level}

    all_tools: list[dict[str, Any]] = []
    if server_tools:
        for st in server_tools:
            mapped = GOOGLE_SERVER_TOOL_MAP.get(st)
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
        agent=_FRONTIER_AGENT["gemini"],
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        stop_sequences=stop_sequences,
        seed=seed,
        thinking=thinking_dict,
        tools=all_tools or None,
        tool_choice=tool_choice,
        response_format=response_format,
        provider_options=extra.get("provider_options"),
        remote_mcp=remote_mcp,
    )
    if isinstance(req, dict):
        return req
    return (full_model, req)


_FRONTIER_ROUTERS: dict[
    FrontierKind,
    Callable[..., dict[str, Any] | tuple[str, Any]],
] = {
    "grok": _frontier_grok,
    "claude": _frontier_claude,
    "openai": _frontier_openai,
    "gemini": _frontier_gemini,
}


def register_frontier_tools(mcp: FastMCP) -> None:
    """Register the unified ``frontier_generate`` primary tool."""

    @mcp.tool(title="Frontier Generate")
    def frontier_generate(
        frontier: FrontierKind,
        messages: list[dict[str, Any]],
        model: str | None = None,
        system: str = "",
        max_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        stop_sequences: list[str] | None = None,
        seed: int | None = None,
        response_format: dict[str, Any] | None = None,
        reasoning_effort: ReasoningEffort | None = None,
        thinking: Literal["adaptive", "disabled"] | dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        server_tools: list[str] | None = None,
        boot: BootLevel = "mcp",
        boot_ref: str | None = None,
        remote_mcp: bool = False,
        include_raw: bool = False,
        timeout: float | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Unified frontier LLM generation — routes to grok/claude/openai/gemini.

        Pick the provider with ``frontier``. Each provider routes to its native
        Stargate endpoint (xai/anthropic/openai/google) with provider-specific
        reasoning/thinking shapes, server-tool maps, and boot-ref defaults.

        **Boot and tools** (two orthogonal axes, consistent across providers):

        - ``boot`` controls system prompt / persona:
          - ``"mcp"`` (default) — loads API subagent seed; Cortex/RAG read tools
            injected (cortex_entity_get, cortex_search_entities,
            cortex_assertions, cortex_deadlines, rag_search).
          - ``"none"`` — no system prompt, no tool definitions.
          - ``"team"`` / ``"full"`` — agent birth prompt (Oppie/API Claude/
            Orion/Bard) + operational context + full tool surface: all
            ``"mcp"`` tools plus ``cortex`` (unified dispatch — assert,
            observe, supersede, journal_write, edge_create, search, and
            more) and ``agent_bus`` (fetch, reply, post threads).
            ``"full"`` also adds Cortex boot narrative.
        - Tool loop runs client-side by default for every frontier and boot
          level — ``libs/agent_seat/native_loop`` resolves MCP tools locally
          and feeds the results back into the provider call. Pass
          ``remote_mcp=True`` to opt into provider-side tool loops instead
          (Anthropic ``body.mcp_servers``, OpenAI/xAI Responses
          ``body.tools[{type:"mcp"}]``). Gemini has no native remote-MCP
          protocol and rejects ``remote_mcp=True``.
        - ``remote_mcp=True`` on grok with the default model auto-swaps to
          ``grok-4.20-multi-agent-0309`` (the only xAI model accepting
          server-side tool loops); on any other frontier or model the caller
          keeps the model they asked for.

        **xAI multi-agent beta restriction**: only
        ``grok-4.20-multi-agent-0309`` accepts remote-MCP tools on xAI;
        other 4.20 variants reject client-side tool arrays with the
        ``Client-side tools for multi-agent models require beta access``
        400. The client-side default here avoids that surface entirely —
        ``frontier_generate(frontier="grok", boot="team")`` now runs the
        Oppie birth prompt with local tool resolution and never touches
        xAI's remote-MCP server. Use ``remote_mcp=True`` only when
        provider-side tool loops are explicitly needed (cache affinity,
        provider-native server tools, or xAI multi-agent when its live bug
        has cleared).

        **Reasoning controls (unified)**:

        ``reasoning_effort ∈ {minimal, low, medium, high, max}``. Maps to:
        - ``grok``: ``thinking.effort`` for low/medium/high (minimal/max dropped)
        - ``claude``: ``effort`` for low/medium/high/max (minimal dropped);
          orthogonal ``thinking`` param controls extended-thinking shape
        - ``openai``: ``reasoning.effort`` for minimal/low/medium/high (max dropped)
        - ``gemini``: ``thinkingConfig.level`` uppercased for low/medium/high
          (minimal/max dropped)

        Dropped values silently fall through — documented rather than hard-failing
        so callers can write portable calls across providers.

        **Provider-specific kwargs via ``extra``**:

        - grok: ``include_encrypted_reasoning: bool``, ``conversation_id: str``,
          ``reasoning_trace: list[dict]``
        - claude: ``speed: "fast"`` (claude-sonnet-4), ``provider_options: dict``
        - openai: ``provider_options: dict``
        - gemini: ``provider_options: dict``

        Unknown ``extra`` keys are passed through to the provider router; the
        router uses only keys it recognizes for the selected frontier.

        **When to use this tool vs ``pipeline(op="async", …)``**:

        The MCP client read-timeout ceiling is ~300s — Anthropic's MCP SDK
        ``ClientSession._send_request`` default, not reconfigurable from the
        server side. ``frontier_generate`` runs synchronously through that
        client channel; any call that exceeds 300s wall-clock is cancelled
        client-side regardless of the provider-side ``timeout`` parameter,
        and the in-flight inference is aborted.

        Use ``frontier_generate`` when:
        - Expected latency < 5 minutes (most ``boot="mcp"`` / ``boot="none"``
          calls, quick consultations, structured-output tasks, streaming
          replies)
        - The result must land in the caller's current MCP turn (tool
          output consumed immediately by the enclosing agent loop)

        Use ``pipeline(op="async", pipeline_id="frontier-dispatch", …)`` instead
        when:
        - Expected latency ≥ 5 minutes (Orion-grade ``reasoning_effort=high``
          on gpt-5.4, Claude opus extended thinking with large
          ``budget_tokens``, deep consensus runs)
        - The call is a fire-and-forget dispatch and the result will be
          polled via ``pipeline(op="result", execution_id=..., wait_seconds=60)``
          or (phase 2) delivered to agent-bus via ``result_delivery``
        - The dispatch must survive the caller's MCP session ending
          (execution runs detached in a Stargate background task)

        Current async-dispatch pipeline surface: unified ``frontier-dispatch``
        covering all four providers. Persona is a runtime option:
        ``pipeline_options={"model":"<provider/model>","agent":"orion|oppie|bard|web"}``
        for team-seat mode (Cortex hydration + team toolset), omit ``agent``
        for persona-free native dispatch.

        **Unique per-provider capabilities**:

        - grok: ``x_search`` server tool (X/Twitter, $5/1k), ``conversation_id``
          for multi-turn server-side state, 2M ctx on grok-4.20.
        - claude: extended thinking with ``budget_tokens`` on claude-opus-4,
          ``speed="fast"`` latency tier, native PDF via document blocks,
          structured citations, computer-use beta.
        - openai: Responses-API routing (reasoning content available),
          code-interpreter / file-search / web-search-preview server tools.
          **NOT for search models** — ``*-search-*`` variants are Chat
          Completions only; use ``llm_generate`` for those.
        - gemini: ``google_search`` grounding, ``code_execution`` sandbox,
          multimodal (text/image/audio/video).

        **Model selection defaults** (omit ``model`` to use):

        - grok: ``grok-4.20-0309-reasoning`` (top reasoning, 2M ctx)
        - claude: ``claude-sonnet-4-6`` (adaptive thinking, 16k output)
        - openai: ``gpt-5.4`` (best intelligence + agentic)
        - gemini: ``gemini-2.5-flash`` (fast, thinking-capable)
        """
        effective_model = model if model is not None else _FRONTIER_DEFAULTS[frontier]
        extra_dict = extra or {}

        router = _FRONTIER_ROUTERS[frontier]
        outcome = router(
            messages=messages,
            model=effective_model,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            stop_sequences=stop_sequences,
            seed=seed,
            response_format=response_format,
            reasoning_effort=reasoning_effort,
            thinking=thinking,
            tools=tools,
            tool_choice=tool_choice,
            server_tools=server_tools,
            boot=boot,
            boot_ref=boot_ref,
            remote_mcp=remote_mcp,
            extra=extra_dict,
        )
        if isinstance(outcome, dict):
            return outcome
        full_model, req = outcome
        return execute_frontier(
            model=full_model,
            req=req,
            include_raw=include_raw,
            tool_name="frontier_generate",
            timeout=timeout,
        )

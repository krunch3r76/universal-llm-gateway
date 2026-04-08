"""Frontier generation tools — provider-specific MCP surfaces.

``grok_generate`` and ``claude_generate`` are the primary tools with
provider-aware signatures and defaults.  ``frontier_generate`` stays as a
backward-compatible dispatch that routes to the correct provider.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from model_id import ModelId

from ._frontier_core import (
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
_GROK_OPPIE_MODELS: set[str] = {"grok-4.20-multi-agent"}

_CLAUDE_BOOT_REF_DEFAULTS: dict[str, str] = {
    "mcp": "notes/system/prompts/api-claude-seed-v1.0.md",
    "team": "notes/system/prompts/api-claude-seed-v1.0.md",
    "full": "notes/system/prompts/claude-seed-full-v1.0.md",
}


def register_frontier_tools(mcp: FastMCP) -> None:
    """Register grok_generate, claude_generate, and frontier_generate (compat)."""

    @mcp.tool()
    def grok_generate(
        messages: list[dict[str, Any]],
        model: str = "grok-4.20-multi-agent",
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
        inject_mcp: bool | None = None,
        boot: str = "mcp",
        boot_ref: str | None = None,
        include_raw: bool = False,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Generate with xAI Grok models via Stargate provider-native endpoint.

        **Model routing** (two orthogonal axes — model selects capability,
        boot selects identity/context):

        - ``grok-4.20-multi-agent`` + boot=mcp/team/full → Oppie persona seed
          auto-loaded. Use for multi-agent coordination, Triad consultation.
        - ``grok-4.20`` (reasoning) → neutral, no persona seed. Use for deep
          chain-of-thought reasoning without persona overhead.
        - ``grok-3-mini`` → neutral, no persona seed. Use for quick advisory
          checks via agent_consult.

        Default to neutral reasoning Grok unless you specifically need Oppie's
        team-lead context, tool mastery enforcement, or multi-agent orchestration.
        Any model can still force a persona seed via explicit ``boot_ref``.

        Full docs: fs(op="md_read", sandbox="project", path="universal-llm-gateway/docs/tool-reference.md", section="grok_generate")
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
            inject_mcp=inject_mcp,
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

    @mcp.tool()
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
        inject_mcp: bool | None = None,
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

        MCP injection is enabled unless the caller opts out with
        ``boot="none"`` or ``inject_mcp=False``.

        ``timeout`` overrides the read timeout in seconds (default 600, max 1800).
        Use higher values for subagent dispatches with boot="full" or heavy
        tool-use workloads that may exceed the default ceiling.

        Full docs: fs(op="md_read", sandbox="project", path="universal-llm-gateway/docs/tool-reference.md", section="claude_generate")
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
            inject_mcp=inject_mcp,
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

    @mcp.tool()
    def frontier_generate(
        messages: list[dict[str, Any]],
        model: str = "anthropic/claude-sonnet-4",
        system: str = "",
        max_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        stop_sequences: list[str] | None = None,
        seed: int | None = None,
        stream: bool = False,
        response_format: dict[str, Any] | None = None,
        thinking: dict[str, Any] | None = None,
        effort: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        inject_mcp: bool | None = None,
        provider_options: dict[str, Any] | None = None,
        conversation_id: str | None = None,
        reasoning_trace: list[dict[str, Any]] | None = None,
        boot: str = "none",
        boot_ref: str | None = None,
        include_raw: bool = False,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Backward-compatible generation — routes to grok_generate or claude_generate.

        Full docs: fs(op="md_read", sandbox="project", path="universal-llm-gateway/docs/tool-reference.md", section="frontier_generate")
        """
        if stream:
            return {"error": "Streaming not yet implemented for frontier_generate"}

        parsed = ModelId.parse(model)
        if parsed.routing_layer == "openrouter":
            return {"error": "OpenRouter routing not yet implemented"}

        req = build_frontier_request(
            model=model,
            messages=messages,
            system=system,
            boot=boot,
            boot_ref=boot_ref,
            inject_mcp=inject_mcp,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            stop_sequences=stop_sequences,
            seed=seed,
            thinking=thinking,
            effort=effort,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
            conversation_id=conversation_id,
            reasoning_trace=reasoning_trace,
            provider_options=provider_options,
        )
        if isinstance(req, dict):
            return req
        return execute_frontier(
            model=model, req=req, include_raw=include_raw, timeout=timeout
        )

"""Tool configuration helpers for ``frontier_dispatch_v1``.

Extracted from ``frontier_dispatch.py`` to keep that module under the SLOC
ceiling.  Contains:

- ``XAI_BUILTIN_TOOLS`` — server-side built-in tool descriptors for xAI.
- ``resolve_default_tools`` — resolve curated tool names from live MCP catalog
  with static-definition fallback (used for both team and read-only tiers).
- ``resolve_dispatch_tool_set`` — 3-way tool + system + hydration resolution
  for endpoint-supplied, persona-bound, and persona-free dispatch modes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)

# xAI server-side built-in tools — injected automatically for Oppie dispatches.
# Distinct from client-side MCP tools: these run on xAI's servers and do not
# require a client tool-call round-trip.  xAI multi-agent models reject
# OpenAI-style function tool definitions, so ``tools`` is always [] for xAI;
# server-side capability comes through ``provider_options.xai.tools`` instead.
#
# ``file_search`` (collections search) is excluded from the default set because
# it requires ``vector_store_ids`` to be functional; injecting it without
# configured collections produces a useless model affordance.  Reintroduce when
# a vector-store config layer exists.
#
# Suppression / override: pass ``generation_parameters.provider_options.xai.tools``
# explicitly (including ``[]`` to suppress all server-side tools).  ``mcp=False``
# also suppresses injection — it is the unified "no tools" signal for xAI personas.
# Explicit ``pipeline_options.tools`` via /api/v1/frontier/dispatch also prevents injection.
XAI_BUILTIN_TOOLS: list[dict[str, Any]] = [
    {"type": "web_search"},
    {"type": "x_search"},
    {"type": "code_interpreter"},
]


async def resolve_default_tools(
    names: tuple[str, ...],
    *,
    fallback: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Resolve curated tool names from the live MCP catalog with static fallback.

    Builds a priority-ordered resolution: static definitions win for names
    present in both catalogs (predictable for well-known tools); live MCP
    catalog fills in any names absent from static definitions.  Missing names
    are logged as warnings (catalog gap, not a hard error — MCP may be
    temporarily unreachable).
    """
    from agent_seat import (
        STATIC_TOOL_FALLBACK,
        get_mcp_tool_definitions,
    )

    static_defs = {
        d.get("function", {}).get("name", ""): d
        for d in STATIC_TOOL_FALLBACK
    }
    live_defs = {
        d.get("function", {}).get("name", ""): d
        for d in await get_mcp_tool_definitions()
    }
    resolved = [
        static_defs[name] if name in static_defs else live_defs[name]
        for name in names
        if name in static_defs or name in live_defs
    ]
    missing = [
        name for name in names if name not in static_defs and name not in live_defs
    ]
    if missing:
        logger.warning(
            "frontier dispatch default tools missing from static/live catalogs: %s",
            missing,
        )
    return resolved or fallback


async def resolve_dispatch_tool_set(
    *,
    mcp_enabled: bool,
    remote_mcp: bool,
    opt_tools: Any,
    agent: str | None,
    model: str,
    provider: str,
    team_tool_names: tuple[str, ...],
    endpoint_request_id: str | None,
    system_prompt: str,
    publish: Callable[..., None],
    execution_id: str,
    boot_profile: str = "light",
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Resolve (tools, system, hydration_meta) for the three dispatch modes.

    Three cases:
    - Endpoint-supplied: ``opt_tools`` is a ``list`` (from ``/api/v1/frontier/dispatch``).
      Soft invariant violation per Kaywan 2026-05-01 (Cortex assertion 7974):
      tools are not a caller concern; the catalog is universal. Honored for
      compatibility but emits ``pipeline.frontier.dispatch.tool.list.supplied``.
    - Persona-bound: ``agent`` is set — hydrates agent and selects tool tier.
      xAI: multi-agent models get ``tools=[]`` (server-side built-ins injected
      via ``provider_options``); non-multi-agent models get the full live MCP
      catalog.
    - Persona-free: no agent — full live MCP catalog when ``mcp_enabled``,
      same surface as persona-bound generic-provider dispatch. The dispatch
      path (frontier HTTP vs team HTTP / MCP ``team_dispatch``) no longer determines
      the tool surface; ``mcp=False`` or ``remote_mcp`` is the suppression
      signal.
    """
    from agent_seat import (
        STATIC_TOOL_FALLBACK,
        assemble_system_prompt,
        get_mcp_tool_definitions,
        hydrate_agent,
        resolve_tool_definitions,
    )

    from ..events.dispatch import (
        PipelineFrontierDispatchHydrated,
        PipelineFrontierDispatchToolListSupplied,
        PipelineFrontierDispatchToolSuppressed,
    )

    if isinstance(opt_tools, list):
        # Case 1: endpoint-supplied tool list (frontier HTTP with explicit tools).
        # Soft invariant: the dispatch infrastructure exposes the full MCP catalog
        # by default when ``mcp=True``. Callers that pass an explicit ``tools`` list
        # are pinning a narrower set than the system would otherwise provide; this
        # is honored for back-compat but logged + observed so the pattern can be
        # surfaced and retired (assertion 7974).
        if agent and not endpoint_request_id:
            raise ValueError(
                "pipeline_options.tools with pipeline_options.agent is only "
                "supported via POST /api/v1/frontier/dispatch; raw "
                "pipeline(pipeline_id=\"frontier-dispatch\") cannot "
                "validate persona tool policy"
            )
        resolved_names = [str(name) for name in opt_tools if isinstance(name, str)]
        if len(resolved_names) != len(opt_tools):
            raise ValueError("pipeline_options.tools must be a list[str]")
        logger.warning(
            "frontier dispatch caller supplied explicit tools list "
            "(soft invariant violation; catalog is universal when mcp=True): "
            "execution_id=%s agent=%s tool_count=%d",
            execution_id,
            agent or "",
            len(resolved_names),
        )
        publish(
            PipelineFrontierDispatchToolListSupplied(
                execution_id=execution_id,
                agent=agent,
                model=model,
                provider=provider,
                tool_count=len(resolved_names),
                tool_names=resolved_names,
            ),
        )
        if not mcp_enabled or remote_mcp:
            tools: list[dict[str, Any]] = []
        else:
            tools = await resolve_tool_definitions(resolved_names)
        hydration_meta = {
            "agent": agent,
            "tool_resolution": "endpoint-supplied",
        }
        return tools, system_prompt, hydration_meta

    if agent:
        # Case 2: persona-bound dispatch — hydrate agent and select tools by provider.
        # Tools are resolved first so the mcp_tool_loop predicate
        # (bool(tools) and not remote_mcp) is known before system-prompt assembly,
        # allowing CORTEX_TOOL_QUICKREF to be suppressed when the model will have
        # no client-side Cortex tool available.
        #
        # Soft boot: mirror frontier_consult.service.build_dispatch_body's
        # ``profile="light"`` choice. The endpoint already assembled an upstream
        # system prompt (passed in as ``system_prompt`` here) and resolve_system_prompt
        # forwards it via pipeline_options["system"]; this function then re-assembles
        # with extra_system=system_prompt. If the two hydration calls used different
        # profiles, the heavier one would dominate the final dispatched prompt
        # because ``assemble_system_prompt`` appends both briefings.
        # Profile selection: virtual-model agent-seat pipelines may opt into
        # the heavier "default" briefing (deadlines + review queue + 3 sessions
        # + 5 self-reflections) via step.boot_profile. team_dispatch /
        # team/frontier HTTP admission paths leave this at "light" — the
        # comment block above on double-hydration explains why.
        bundle = await hydrate_agent(agent, profile=boot_profile, model=model)
        publish(
            PipelineFrontierDispatchHydrated(
                agent=agent,
                execution_id=execution_id,
                briefing_bytes=bundle.section_counts.get("briefing_bytes", 0),
                section_counts=bundle.section_counts,
                continuation_id=bundle.continuation_id,
            )
        )
        # Role-tier suppression: role:{slug}.attributes.capability_tier
        # may be set to "inline-only" to revoke the tool surface for an agent
        # regardless of provider/model. Orthogonal to the provider-derived
        # xai-multi-agent suppression below — that gate is keyed on the model
        # rejecting client-side tools at the API level; this gate is keyed on
        # the agent itself being demoted to inline-substrate operation. The
        # CORTEX_TOOL_QUICKREF block downstream is suppressed automatically
        # via ``include_cortex_quickref=bool(tools)``.
        if bundle.agent_meta.capability_tier == "inline-only":
            tools = []
            publish(
                PipelineFrontierDispatchToolSuppressed(
                    execution_id=execution_id,
                    agent=agent,
                    model=model,
                    provider=provider,
                    reason="capability_tier_inline_only",
                ),
            )
        elif remote_mcp or not mcp_enabled:
            tools = []
        elif provider == "xai" and "multi-agent" in model:
            # Multi-agent xAI models reject client-side function tools;
            # server-side built-ins are injected via provider_options.
            # Non-multi-agent xAI models fall through to the generic path.
            tools = []
        elif provider == "anthropic":
            tools = await resolve_default_tools(
                team_tool_names,
                fallback=STATIC_TOOL_FALLBACK,
            )
        else:
            live = await get_mcp_tool_definitions()
            tools = live or STATIC_TOOL_FALLBACK
        assembled_system = assemble_system_prompt(
            agent,
            briefing_card_md=bundle.briefing_card_md,
            continuation_md=bundle.continuation_md,
            extra_system=system_prompt,
            include_cortex_quickref=bool(tools) and not remote_mcp,
            inline_only=bundle.inline_only,
        )
        hydration_meta = {
            "agent": agent,
            "section_counts": bundle.section_counts,
            "continuation_id": bundle.continuation_id,
        }
        return tools, assembled_system, hydration_meta

    # Case 3: persona-free dispatch — full live MCP catalog when mcp_enabled.
    # Aligned with Case 2's generic-provider branch so the dispatch path
    # (frontier HTTP vs team/MCP) no longer determines the tool
    # surface — closes the BOE-19-P-vintage divergence where persona-free HTTP
    # exposed only ("cortex", "rag") while team_dispatch exposed the full
    # catalog. ``mcp=False`` or ``remote_mcp`` remains the suppression signal.
    if not mcp_enabled or remote_mcp:
        tools = []
    else:
        live = await get_mcp_tool_definitions()
        tools = live or STATIC_TOOL_FALLBACK
    return tools, system_prompt, {"agent": None}

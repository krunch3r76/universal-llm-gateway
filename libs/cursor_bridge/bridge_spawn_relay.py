"""Glass spawn relays for the cursor_bridge MCP tool (no MCP event recording)."""

from __future__ import annotations

from typing import Any

from cursor_bridge.cursor_agents_hop import (
    cursor_debug_url_for_gui_host,
    launch_cursor_agents_tab,
)
from cursor_bridge.spawn_house import spawn_house as spawn_house_impl
from cursor_bridge.spawn_model_knobs import merge_spawn_model_knobs


def relay_spawn_tab(
    *,
    gui_host: str,
    prompt: str,
    model: str | None,
    effort: str | None = None,
    fast: bool | str | None = None,
    model_knobs: dict[str, str] | None = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    """Launch one Glass agent tab with optional turn-1 knobs and composer id."""
    debug_url = cursor_debug_url_for_gui_host(gui_host)
    knobs = merge_spawn_model_knobs(model_knobs, effort=effort, fast=fast)
    requested = str(agent_id or "").strip() or None
    res = launch_cursor_agents_tab(
        prompt,
        model_query=str(model or "").strip(),
        model_knobs=knobs,
        debug_url=debug_url,
        requested_agent_id=requested,
    )
    return {
        **res,
        "op": "spawn_tab",
        "gui_host": gui_host,
        "_knobs": knobs,
        "_requested_agent_id": requested,
    }


def relay_spawn_house(
    *,
    gui_host: str,
    slug: str,
    prompt: str,
    model: str | None,
    effort: str | None = None,
    fast: bool | str | None = None,
    model_knobs: dict[str, str] | None = None,
    agent_id: str | None = None,
    work_key: str | None = None,
) -> dict[str, Any]:
    """Atomic house birth relay for ``cursor_bridge(op=spawn_house)``."""
    res = spawn_house_impl(
        slug=slug,
        prompt=prompt,
        gui_host=gui_host,
        model=model,
        effort=effort,
        fast=fast,
        model_knobs=model_knobs,
        agent_id=agent_id,
        work_key=work_key,
    )
    return {**res, "op": "spawn_house", "gui_host": gui_host}


__all__ = ["relay_spawn_house", "relay_spawn_tab"]

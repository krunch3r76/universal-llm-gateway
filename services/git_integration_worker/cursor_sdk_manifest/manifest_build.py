"""Orchestrator that builds an ``EffectsManifest`` from conversation (and optional) sources.

Single public function ``build_effects_manifest``: classify branch, allocate
surfaces in ``_SURFACE_ORDER``, fold toolCall entries, optionally merge MCP
events (branch B) and wrapper effects, compute coverage, then
``ensure_subagents_surface``. Invariant: never raises on unparsed wire dicts
(docstring on the function is load-bearing). Depends on ``surface_taxonomy``
and ``effect_entries`` plus ``ensure_subagents_surface`` from
``cursor_sdk_subagent_capture``. Must not import ``manifest_merge`` (that
module calls ``build_effects_manifest``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from implement_admission.closeout_models import (
    EffectEntry,
    EffectsManifest,
    SurfaceSection,
)

from services.git_integration_worker.cursor_sdk_subagent_capture import (
    ensure_subagents_surface,
)

from . import effect_entries, surface_taxonomy


def build_effects_manifest(
    *,
    dispatch_id: str,
    thread_id: str,
    turns: Iterable,
    mcp_events: list[Mapping[str, Any]] | None = None,
    wrapper_effects: dict[str, list[EffectEntry]] | None = None,
    capture_branch: surface_taxonomy.CaptureBranch | None = None,
    contract: str | None = None,
) -> EffectsManifest:
    """Pure manifest builder — never raises on unparsed wire dicts."""
    branch = capture_branch or effect_entries.classify_mcp_capture_branch(turns)
    sources: list[str] = ["conversation"]
    surfaces: dict[str, SurfaceSection] = {
        name: SurfaceSection(surface=name, source="conversation", entries=[])
        for name in surface_taxonomy._SURFACE_ORDER
    }

    for message in effect_entries._iter_tool_call_messages(turns):
        entry = effect_entries._entry_from_tool_call(message)
        if entry is None:
            continue
        surface = effect_entries._surface_for_tool_call(message, entry)
        if surface is None:
            continue
        surfaces[surface].entries.append(entry)

    if branch == "B" and mcp_events:
        sources.append("mcp_events")
        effect_entries._merge_mcp_event_entries(surfaces, mcp_events)

    if wrapper_effects:
        sources.append("wrapper")
        for surface, entries in wrapper_effects.items():
            section = surfaces.get(surface)
            if section is None:
                continue
            merged = list(section.entries)
            merged.extend(entries)
            surfaces[surface] = SurfaceSection(
                surface=surface,
                source="wrapper" if not section.entries else section.source,
                entries=merged,
                cross_check=section.cross_check,
            )

    coverage = {
        name: surface_taxonomy._surface_coverage(section)
        for name, section in surfaces.items()
        if section.entries
    }
    service_section = surfaces.get("service")
    if service_section and any(
        entry.op == "dispatch" for entry in service_section.entries
    ):
        coverage["service"] = "partial"
    manifest = EffectsManifest(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        capture_sources=sources,
        surfaces={k: v for k, v in surfaces.items() if v.entries},
        coverage=coverage,
        contract=contract,
    )
    return ensure_subagents_surface(manifest)

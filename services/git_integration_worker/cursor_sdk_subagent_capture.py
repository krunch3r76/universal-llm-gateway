"""Subagent / Task tool capture for cursor-sdk effects_manifest (item 9 / AC-9b)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from implement_admission.closeout_models import (
    EffectEntry,
    EffectsManifest,
    SurfaceSection,
)

from services.git_integration_worker.cursor_sdk_stream_capture import (
    ToolCallObservation,
)
from services.git_integration_worker.cursor_sdk_surface_authority import (
    authority_for_surface_source,
    mixed_source_cross_check,
)

SUBAGENTS_SURFACE = "subagents"
_SUBAGENT_TOOL_NAMES = frozenset({"task"})
_DETAIL_CAP = 500


def is_subagent_tool_call(*, tool_type: str = "", tool_name: str = "") -> bool:
    """True when the wire tool is Cursor's Task / subagent delegator."""
    if tool_type.lower() in _SUBAGENT_TOOL_NAMES:
        return True
    return tool_name.lower() in _SUBAGENT_TOOL_NAMES


def subagent_type_from_stream_args(tool_name: str, args: Any) -> str | None:
    if not is_subagent_tool_call(tool_name=tool_name):
        return None
    if not isinstance(args, Mapping):
        return None
    value = args.get("subagent_type")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _string_arg(args: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _bounded_subagent_detail(args: Mapping[str, Any]) -> dict[str, Any] | None:
    keep: dict[str, Any] = {}
    for key in ("subagent_type", "description", "model", "run_in_background"):
        if key not in args:
            continue
        value = args[key]
        if value is None:
            continue
        keep[key] = value
    if not keep:
        return None
    try:
        text = json.dumps(keep, separators=(",", ":"))
    except (TypeError, ValueError):
        return {"raw": str(keep)[:_DETAIL_CAP]}
    if len(text) <= _DETAIL_CAP:
        return keep
    return {"truncated": text[:_DETAIL_CAP]}


def entry_from_subagent_message(message: Mapping[str, Any]) -> EffectEntry | None:
    """Build a subagents-surface entry from a conversation toolCall message."""
    tool_type = str(message.get("type") or "")
    if not is_subagent_tool_call(tool_type=tool_type):
        return None
    args = message.get("args") if isinstance(message.get("args"), Mapping) else {}
    subagent_type = _string_arg(args, "subagent_type")
    call_id = _string_arg(message, "call_id", "callId", "id")
    identity = call_id or subagent_type
    return EffectEntry(
        op="Task",
        target=subagent_type,
        detail=_bounded_subagent_detail(args),
        identity=identity,
    )


def _existing_subagent_identities(section: SurfaceSection | None) -> set[str]:
    if section is None:
        return set()
    ids: set[str] = set()
    for entry in section.entries:
        ident = entry.identity or entry.target
        if ident:
            ids.add(ident)
    return ids


def merge_stream_subagent_calls(
    manifest: EffectsManifest | None,
    tool_calls: tuple[ToolCallObservation, ...],
) -> EffectsManifest | None:
    """Fold stream-observed Task invocations missing from the conversation manifest."""
    task_calls = [
        tc
        for tc in tool_calls
        if is_subagent_tool_call(tool_name=tc.tool_name)
        and str(tc.status).lower() in {"completed", "success", "finished"}
    ]
    if manifest is None and not task_calls:
        return None
    if not task_calls:
        return ensure_subagents_surface(manifest) if manifest is not None else manifest

    dispatch_id = manifest.dispatch_id if manifest else "unknown"
    thread_id = manifest.thread_id if manifest else "unknown"
    section = manifest.surfaces.get(SUBAGENTS_SURFACE) if manifest else None
    known = _existing_subagent_identities(section)
    new_entries: list[EffectEntry] = []
    for tc in task_calls:
        identity = tc.call_id or tc.subagent_type
        if not identity or identity in known:
            continue
        known.add(identity)
        detail: dict[str, Any] | None = None
        if tc.subagent_type:
            detail = {"subagent_type": tc.subagent_type}
        if tc.status:
            detail = {**(detail or {}), "status": tc.status}
        new_entries.append(
            EffectEntry(
                op="Task",
                target=tc.subagent_type,
                detail=detail,
                identity=identity,
            )
        )

    if not new_entries and manifest is not None:
        return ensure_subagents_surface(manifest)

    merged_surfaces = dict(manifest.surfaces) if manifest else {}
    existing = merged_surfaces.get(SUBAGENTS_SURFACE)
    if existing is None:
        auth = authority_for_surface_source(SUBAGENTS_SURFACE, "stream")
        merged_surfaces[SUBAGENTS_SURFACE] = SurfaceSection(
            surface=SUBAGENTS_SURFACE,
            source="stream",
            entries=new_entries,
            authority_class=auth[0],
            absence_semantics=auth[1],
        )
    else:
        cross = mixed_source_cross_check(existing, "stream")
        auth = authority_for_surface_source(
            SUBAGENTS_SURFACE, existing.source, entry_count=len(existing.entries)
        )
        merged_surfaces[SUBAGENTS_SURFACE] = SurfaceSection(
            surface=SUBAGENTS_SURFACE,
            source=existing.source,
            entries=[*existing.entries, *new_entries],
            cross_check=cross or existing.cross_check,
            authority_class=auth[0],
            absence_semantics=auth[1],
        )
    sources = list(manifest.capture_sources) if manifest else []
    sources = list(dict.fromkeys([*sources, "stream"]))
    coverage = dict(manifest.coverage) if manifest else {}
    coverage[SUBAGENTS_SURFACE] = coverage.get(SUBAGENTS_SURFACE, "complete")
    base = manifest or EffectsManifest(dispatch_id=dispatch_id, thread_id=thread_id)
    return base.model_copy(
        update={
            "surfaces": merged_surfaces,
            "capture_sources": sources,
            "coverage": coverage,
        }
    )


def ensure_subagents_surface(manifest: EffectsManifest | None) -> EffectsManifest | None:
    """Always emit subagents surface — explicit empty when no Task invocations (AC-9b)."""
    if manifest is None:
        return None
    if SUBAGENTS_SURFACE in manifest.surfaces:
        return manifest
    merged = dict(manifest.surfaces)
    if "stream" in manifest.capture_sources:
        auth: tuple[str, str] = ("observed", "absence=zero")
        source = "stream"
    else:
        auth = authority_for_surface_source(SUBAGENTS_SURFACE, "capture", entry_count=0)
        source = "capture"
    merged[SUBAGENTS_SURFACE] = SurfaceSection(
        surface=SUBAGENTS_SURFACE,
        source=source,
        entries=[],
        authority_class=auth[0],  # type: ignore[arg-type]
        absence_semantics=auth[1],  # type: ignore[arg-type]
    )
    coverage = dict(manifest.coverage)
    coverage[SUBAGENTS_SURFACE] = "complete"
    return manifest.model_copy(update={"surfaces": merged, "coverage": coverage})


__all__ = [
    "SUBAGENTS_SURFACE",
    "ensure_subagents_surface",
    "entry_from_subagent_message",
    "is_subagent_tool_call",
    "merge_stream_subagent_calls",
    "subagent_type_from_stream_args",
]

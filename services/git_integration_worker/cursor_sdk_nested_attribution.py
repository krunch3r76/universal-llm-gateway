"""Parent dispatch id propagation for Task-spawned nested dispatches (AC-9g)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from implement_admission.closeout_models import (
    EffectEntry,
    EffectsManifest,
    SurfaceSection,
)

_BOUNDARY_SURFACES = frozenset({"cortex", "fs", "agent_bus"})
_SIDECAR_REL = "tmp/reviews/closeouts/{dispatch_id}.md"
_EFFECTS_MANIFEST_HEADING = "## effects_manifest"
_JSON_OBJECT_TAIL_RE = re.compile(r"\{[\s\S]*\}\s*$")


def _parse_closeout_json(sidecar_text: str) -> dict[str, Any] | None:
    start = sidecar_text.find("{")
    if start < 0:
        return None
    try:
        payload = json.loads(sidecar_text[start:])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _raw_effects_manifest_from_sidecar(sidecar_text: str) -> dict[str, Any] | None:
    """Extract effects_manifest dict from closeout sidecar — JSON body or appendix (AC-9k)."""
    payload = _parse_closeout_json(sidecar_text)
    if payload is not None:
        raw = payload.get("effects_manifest")
        if isinstance(raw, dict):
            return raw
    marker_idx = sidecar_text.find(_EFFECTS_MANIFEST_HEADING)
    if marker_idx < 0:
        return None
    tail = sidecar_text[marker_idx + len(_EFFECTS_MANIFEST_HEADING) :].strip()
    match = _JSON_OBJECT_TAIL_RE.search(tail)
    if match is None:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    nested = parsed.get("effects_manifest")
    if isinstance(nested, dict):
        return nested
    if "schema_version" in parsed and "surfaces" in parsed:
        return parsed
    return None


def _child_manifest_from_sidecar(
    source_repo: Path,
    child_dispatch_id: str,
) -> EffectsManifest | None:
    path = source_repo / _SIDECAR_REL.format(dispatch_id=child_dispatch_id)
    if not path.is_file():
        return None
    raw = _raw_effects_manifest_from_sidecar(path.read_text(encoding="utf-8"))
    if raw is None:
        return None
    try:
        return EffectsManifest.model_validate(raw)
    except (TypeError, ValueError):
        return None


def list_nested_child_dispatch_ids(
    ledger: Any,
    *,
    parent_dispatch_id: str,
) -> list[str]:
    """Return child dispatch ids admitted with ``nest_under=parent``."""
    list_fn = getattr(ledger, "list_nested_children", None)
    if not callable(list_fn):
        return []
    return list(list_fn(parent_dispatch_id=parent_dispatch_id))


def _attributed_entry(
    entry: EffectEntry,
    *,
    parent_dispatch_id: str,
    origin_dispatch_id: str,
) -> EffectEntry:
    detail = dict(entry.detail or {})
    detail["attributed_dispatch_id"] = parent_dispatch_id
    detail["origin_dispatch_id"] = origin_dispatch_id
    return entry.model_copy(update={"detail": detail})


def fold_nested_boundary_effects(
    manifest: EffectsManifest | None,
    *,
    parent_dispatch_id: str,
    source_repo: Path | None = None,
    ledger: Any | None = None,
    child_dispatch_ids: list[str] | None = None,
) -> EffectsManifest | None:
    """Fold nested child boundary-crossing effects under the parent dispatch id (AC-9g)."""
    if manifest is None:
        return None
    child_ids = list(child_dispatch_ids or [])
    if ledger is not None and not child_ids:
        child_ids = list_nested_child_dispatch_ids(
            ledger, parent_dispatch_id=parent_dispatch_id
        )
    if not child_ids or source_repo is None:
        return manifest

    merged_surfaces = dict(manifest.surfaces)
    for child_id in child_ids:
        child_manifest = _child_manifest_from_sidecar(source_repo, child_id)
        if child_manifest is None:
            continue
        for surface_name in _BOUNDARY_SURFACES:
            section = child_manifest.surfaces.get(surface_name)
            if section is None or not section.entries:
                continue
            attributed = [
                _attributed_entry(
                    entry,
                    parent_dispatch_id=parent_dispatch_id,
                    origin_dispatch_id=child_id,
                )
                for entry in section.entries
            ]
            existing = merged_surfaces.get(surface_name)
            if existing is None:
                merged_surfaces[surface_name] = SurfaceSection(
                    surface=surface_name,
                    source="nested_child",
                    entries=attributed,
                    authority_class="ledger_attested",
                    absence_semantics="absence=zero",
                )
            else:
                merged_surfaces[surface_name] = existing.model_copy(
                    update={"entries": [*existing.entries, *attributed]}
                )

    sources = list(dict.fromkeys([*manifest.capture_sources, "nested_child"]))
    return manifest.model_copy(
        update={"dispatch_id": parent_dispatch_id, "surfaces": merged_surfaces, "capture_sources": sources}
    )


__all__ = [
    "fold_nested_boundary_effects",
    "list_nested_child_dispatch_ids",
]

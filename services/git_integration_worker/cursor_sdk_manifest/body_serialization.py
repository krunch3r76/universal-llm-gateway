"""Manifest digest, surface counts, and turn-body compaction.

The only module that applies ``MAX_MANIFEST_BODY_PROBE``: when the compact JSON
probe exceeds that budget, ``serialize_effects_manifest_for_body`` returns the
digest stub from ``compact_manifest_for_body`` instead of the full model.
Invariant: if ``sidecar_appendix`` is provided it is mutated in place (append
the full JSON) — do not copy the list. Keep this module separate; folding it
into ``manifest_merge`` would push that file past 300 SLOC.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from implement_admission.closeout_models import EffectsManifest

from . import surface_taxonomy

def manifest_digest(manifest: EffectsManifest) -> str:
    payload = manifest.model_dump(mode="json")
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def manifest_surface_counts(manifest: EffectsManifest) -> dict[str, int]:
    return {name: len(section.entries) for name, section in manifest.surfaces.items()}


def compact_manifest_for_body(
    manifest: EffectsManifest,
) -> EffectsManifest | dict[str, Any]:
    """Digest stub when the full manifest would exceed turn-body budget."""
    compact: dict[str, Any] = {
        "schema_version": manifest.schema_version,
        "dispatch_id": manifest.dispatch_id,
        "thread_id": manifest.thread_id,
        "digest": manifest_digest(manifest),
        "surface_counts": manifest_surface_counts(manifest),
        "capture_sources": manifest.capture_sources,
        "external_effects": manifest.external_effects,
        "contract": manifest.contract,
    }
    if not manifest.surfaces:
        compact["surfaces"] = {}
    return compact
def serialize_effects_manifest_for_body(
    manifest: EffectsManifest | None,
    *,
    sidecar_appendix: list[str] | None = None,
) -> EffectsManifest | dict[str, Any] | None:
    if manifest is None:
        return None
    manifest_json = json.dumps(manifest.model_dump(mode="json"), indent=2)
    if sidecar_appendix is not None:
        sidecar_appendix.append(manifest_json)
    probe = json.dumps(
        {"effects_manifest": manifest.model_dump(mode="json")},
        separators=(",", ":"),
    )
    if len(probe) <= surface_taxonomy.MAX_MANIFEST_BODY_PROBE:
        return manifest
    return compact_manifest_for_body(manifest)

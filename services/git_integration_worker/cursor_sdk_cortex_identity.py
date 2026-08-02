"""Cortex surface identity harvest from boundary responses (item 18 / AC-18a-b).

Conversation capture records MCP tool *arguments*; assertion ids live only in
boundary *responses*. This module patches entry identities without relabeling
surface authority — identity provenance goes in entry detail.
"""

from __future__ import annotations

import json
import re
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
    mixed_source_cross_check,
)

_ASSERTION_IDENTITY_RE = re.compile(r"^assertion:(\d+)$")
_CORTEX_TOOLS = frozenset({"cortex", "cortex_brief"})
_CORTEX_WRITE_OPS = frozenset({"assert", "supersede", "observe", "friction"})


def _string_arg(args: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
    return None


def _nested_tool_arguments(args: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = args.get("arguments")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, Mapping) else {}
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}
    if isinstance(raw, Mapping):
        return raw
    return {}


def _effective_mcp_args(nested: Mapping[str, Any]) -> Mapping[str, Any]:
    inner = _nested_tool_arguments(nested)
    if not inner:
        return nested
    merged: dict[str, Any] = dict(nested)
    merged.update(inner)
    return merged


def _cortex_op_from_args(args: Mapping[str, Any]) -> str | None:
    return _string_arg(args, "tool", "op")


def _unwrap_tool_result(result: object) -> object | None:
    if not isinstance(result, Mapping):
        return result
    if result.get("status") == "error":
        return None
    value = result.get("value")
    if value is not None:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError, ValueError):
                return value
        return value
    return result


def _assertion_id_from_payload(payload: object) -> int | None:
    if not isinstance(payload, Mapping):
        return None
    item = payload.get("item")
    if isinstance(item, Mapping):
        id_val = item.get("id")
        if isinstance(id_val, int) and not isinstance(id_val, bool):
            return id_val
    for key in ("id", "assertion_id"):
        id_val = payload.get(key)
        if isinstance(id_val, int) and not isinstance(id_val, bool):
            return id_val
        if isinstance(id_val, str) and id_val.isdigit():
            return int(id_val)
    return None


def assertion_id_from_cortex_observation(obs: ToolCallObservation) -> int | None:
    """Extract minted assertion id from a completed cortex stream observation."""
    if obs.tool_name.lower() not in _CORTEX_TOOLS or obs.status != "completed":
        return None
    raw_args = obs.args if isinstance(obs.args, Mapping) else {}
    nested = raw_args.get("args") if isinstance(raw_args.get("args"), Mapping) else raw_args
    effective = _effective_mcp_args(nested) if nested else {}
    op = _cortex_op_from_args(effective)
    if op not in _CORTEX_WRITE_OPS:
        return None
    payload = _unwrap_tool_result(obs.result)
    if payload is None:
        return None
    return _assertion_id_from_payload(payload)


def _entity_key_from_observation(obs: ToolCallObservation) -> str | None:
    raw_args = obs.args if isinstance(obs.args, Mapping) else {}
    nested = raw_args.get("args") if isinstance(raw_args.get("args"), Mapping) else raw_args
    effective = _effective_mcp_args(nested) if nested else {}
    return _string_arg(effective, "entity_id", "assertion_id", "id")


def _patch_entry_with_assertion(
    entry: EffectEntry,
    *,
    aid: str,
    obs: ToolCallObservation | None = None,
) -> EffectEntry:
    detail = dict(entry.detail or {})
    detail["identity_harvest_source"] = "boundary_response"
    if entry.identity:
        detail["prior_identity"] = entry.identity
    if obs is not None and obs.call_id:
        detail["boundary_call_id"] = obs.call_id
    return entry.model_copy(
        update={
            "identity": f"assertion:{aid}",
            "detail": detail,
        }
    )


def build_boundary_assertion_index(
    tool_calls: tuple[ToolCallObservation, ...],
) -> dict[str, str]:
    """Map entity slug and boundary call_id keys to assertion id strings."""
    index: dict[str, str] = {}
    for obs in tool_calls:
        aid = assertion_id_from_cortex_observation(obs)
        if aid is None:
            continue
        aid_str = str(aid)
        entity = entity_key_from_observation(obs)
        if entity:
            index[entity] = aid_str
        if obs.call_id:
            index[obs.call_id] = aid_str
    return index


def entity_key_from_observation(obs: ToolCallObservation) -> str | None:
    """Entity slug from a stream observation's MCP args (request side)."""
    return _entity_key_from_observation(obs)


def _entry_write_entity_key(entry: EffectEntry) -> str | None:
    if entry.target and not entry.target.startswith("assertion:"):
        return entry.target
    if entry.identity and not _ASSERTION_IDENTITY_RE.match(entry.identity or ""):
        return entry.identity
    detail = entry.detail if isinstance(entry.detail, Mapping) else {}
    args = detail.get("args") if isinstance(detail.get("args"), Mapping) else detail
    if isinstance(args, Mapping):
        return _string_arg(args, "entity_id", "assertion_id", "id")
    return None


def enrich_cortex_identities_from_stream(
    manifest: EffectsManifest | None,
    tool_calls: tuple[ToolCallObservation, ...],
) -> EffectsManifest | None:
    """Patch conversation cortex entries with assertion ids from boundary responses."""
    if manifest is None or not tool_calls:
        return manifest
    section = manifest.surfaces.get("cortex")
    if section is None or not section.entries:
        return manifest

    index = build_boundary_assertion_index(tool_calls)
    if not index:
        return manifest

    obs_by_entity: dict[str, ToolCallObservation] = {}
    for obs in tool_calls:
        entity = entity_key_from_observation(obs)
        if entity and entity not in obs_by_entity:
            obs_by_entity[entity] = obs

    patched: list[EffectEntry] = []
    changed = False
    for entry in section.entries:
        if entry.identity and _ASSERTION_IDENTITY_RE.match(entry.identity):
            patched.append(entry)
            continue
        entity_key = _entry_write_entity_key(entry)
        aid = index.get(entity_key or "") if entity_key else None
        matching_obs = obs_by_entity.get(entity_key or "") if entity_key else None
        if aid is None and entity_key and len(section.entries) == 1:
            unclaimed_aids = {
                v
                for k, v in index.items()
                if k.startswith("tool_") or k.startswith("stream-")
            }
            if len(unclaimed_aids) == 1:
                aid = next(iter(unclaimed_aids))
                matching_obs = next(
                    (
                        obs
                        for obs in tool_calls
                        if str(assertion_id_from_cortex_observation(obs) or "") == aid
                    ),
                    None,
                )
        if aid is None:
            patched.append(entry)
            continue
        patched.append(_patch_entry_with_assertion(entry, aid=aid, obs=matching_obs))
        changed = True

    if not changed:
        return manifest

    updated_section = section.model_copy(update={"entries": patched})
    if section.source == "conversation":
        updated_section = updated_section.model_copy(
            update={"cross_check": section.cross_check or "identity_harvest:boundary_response"}
        )
    merged_surfaces = dict(manifest.surfaces)
    merged_surfaces["cortex"] = updated_section
    sources = list(dict.fromkeys([*manifest.capture_sources, "stream"]))
    return manifest.model_copy(update={"surfaces": merged_surfaces, "capture_sources": sources})


def merge_stream_cortex_entries(
    manifest: EffectsManifest | None,
    tool_calls: tuple[ToolCallObservation, ...],
) -> EffectsManifest | None:
    """Enrich conversation identities, then fold stream-only cortex write acks."""
    from services.git_integration_worker.cursor_sdk_manifest import (
        harvest_cortex_assertion_ids,
        _cortex_entry_from_stream_observation,
    )

    manifest = enrich_cortex_identities_from_stream(manifest, tool_calls)
    if manifest is None or not tool_calls:
        return manifest

    existing_assertions = set(harvest_cortex_assertion_ids(manifest))
    existing_identities: set[str] = set()
    cortex_section = manifest.surfaces.get("cortex")
    if cortex_section is not None:
        for entry in cortex_section.entries:
            if entry.identity:
                existing_identities.add(entry.identity)

    merged_entries = list(cortex_section.entries) if cortex_section else []
    new_entries: list[EffectEntry] = []
    in_place_patched = False
    for obs in tool_calls:
        if obs.status != "completed":
            continue
        entry = _cortex_entry_from_stream_observation(obs)
        if entry is None:
            continue
        stream_assertion = (
            entry.identity.split(":", 1)[1]
            if entry.identity and entry.identity.startswith("assertion:")
            else None
        )
        entity = entity_key_from_observation(obs)
        if entity and stream_assertion:
            for idx, existing in enumerate(merged_entries):
                existing_key = _entry_write_entity_key(existing)
                if (
                    existing_key == entity
                    and existing.identity
                    and not _ASSERTION_IDENTITY_RE.match(existing.identity)
                ):
                    merged_entries[idx] = _patch_entry_with_assertion(
                        existing, aid=stream_assertion, obs=obs
                    )
                    existing_identities.add(f"assertion:{stream_assertion}")
                    existing_assertions.add(stream_assertion)
                    in_place_patched = True
                    break
            else:
                pass
            if any(
                _entry_write_entity_key(e) == entity
                and e.identity
                and _ASSERTION_IDENTITY_RE.match(e.identity)
                for e in merged_entries
            ):
                continue
        if entry.identity and entry.identity in existing_identities:
            continue
        if stream_assertion and stream_assertion in existing_assertions:
            continue
        new_entries.append(entry)

    if not new_entries and not in_place_patched:
        return manifest

    if cortex_section is None:
        cortex_section = SurfaceSection(surface="cortex", source="stream", entries=[])
    merged_entries.extend(new_entries)
    cross = mixed_source_cross_check(cortex_section, "stream")
    merged_surfaces = dict(manifest.surfaces)
    merged_surfaces["cortex"] = cortex_section.model_copy(
        update={
            "entries": merged_entries,
            "source": cortex_section.source if cortex_section.entries else "stream",
            "cross_check": cross or cortex_section.cross_check,
        }
    )
    sources = list(dict.fromkeys([*manifest.capture_sources, "stream"]))
    coverage = dict(manifest.coverage)
    coverage["cortex"] = "complete"
    return manifest.model_copy(
        update={"surfaces": merged_surfaces, "capture_sources": sources, "coverage": coverage}
    )


def surfaces_with_request_response_identity_gap() -> dict[str, str]:
    """AC-18c: surfaces where identity may require boundary response, not just request."""
    return {
        "cortex": (
            "Write-family ops mint assertion ids in MCP response (item.id); "
            "conversation capture stores request args only — fixed by boundary harvest."
        ),
        "fs": (
            "Path/op in request; write ack fields (written_sha256, replaced_sha256) "
            "live in response — identity stays path-based so harvest gap is metadata-only."
        ),
        "agent_bus": (
            "Thread slug in request; turn numbers / message ids may appear only in "
            "response — partial gap when turn pin is response-only."
        ),
        "rag": (
            "Query/scope in request; chunk ids / mapped URIs may be response-only — "
            "identity uses request fields when present."
        ),
        "repo": "File paths and ops are request-complete — no assertion-style response gap.",
        "subagents": "Primarily stream-observed (Task wire) — observed authority path.",
        "service": "Opaque dispatch; response correlation varies by tool — no stable id harvest.",
    }


__all__ = [
    "assertion_id_from_cortex_observation",
    "build_boundary_assertion_index",
    "entity_key_from_observation",
    "enrich_cortex_identities_from_stream",
    "merge_stream_cortex_entries",
    "surfaces_with_request_response_identity_gap",
]

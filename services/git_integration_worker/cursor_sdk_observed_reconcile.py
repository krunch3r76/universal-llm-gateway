"""Observed stream/conversation vs commit-ack reconciliation (item 9 / AC-9f)."""

from __future__ import annotations

import re
from collections.abc import Mapping

from implement_admission.closeout_models import (
    EffectEntry,
    EffectsManifest,
    ObservedReconciliation,
)

from services.git_integration_worker.cursor_sdk_cortex_identity import (
    build_boundary_assertion_index,
)
from services.git_integration_worker.cursor_sdk_stream_capture import (
    ToolCallObservation,
)

_ASSERTION_IDENTITY_RE = re.compile(r"^assertion:(\d+)$")
_CORTEX_TOOL_NAMES = frozenset({"cortex", "cortex_brief"})
_CORTEX_WRITE_OPS = frozenset({"assert", "supersede", "observe", "friction"})


def _cortex_write_op_from_observation(obs: ToolCallObservation) -> bool:
    return obs.tool_name.lower() in _CORTEX_TOOL_NAMES


def _committed_assertion_ids(section_entries: list[EffectEntry]) -> set[str]:
    ids: set[str] = set()
    for entry in section_entries:
        ident = entry.identity or ""
        match = _ASSERTION_IDENTITY_RE.match(ident)
        if match:
            ids.add(match.group(1))
    return ids


def _observed_cortex_call_keys(tool_calls: tuple[ToolCallObservation, ...]) -> set[str]:
    keys: set[str] = set()
    for obs in tool_calls:
        if not _cortex_write_op_from_observation(obs):
            continue
        keys.add(obs.call_id or obs.target_path or obs.tool_name)
    return keys


def _entry_entity_slug(entry: EffectEntry) -> str | None:
    ident = entry.identity or entry.target or ""
    if ident and not _ASSERTION_IDENTITY_RE.match(ident):
        return str(ident)
    detail = entry.detail if isinstance(entry.detail, Mapping) else {}
    args = detail.get("args") if isinstance(detail.get("args"), Mapping) else detail
    if isinstance(args, Mapping):
        for key in ("entity_id", "assertion_id", "id"):
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _seat_claimed_without_ack(
    entries: list[EffectEntry],
    committed: set[str],
    *,
    entity_to_aid: dict[str, str],
) -> list[str]:
    unobserved: list[str] = []
    for entry in entries:
        if entry.op not in _CORTEX_WRITE_OPS and entry.op not in _CORTEX_TOOL_NAMES:
            detail = entry.detail or {}
            op = str(detail.get("op") or detail.get("tool") or entry.op or "")
            if op not in _CORTEX_WRITE_OPS:
                continue
        ident = entry.identity or entry.target or entry.op
        if ident and ident.startswith("assertion:"):
            aid = ident.split(":", 1)[1]
            if aid not in committed:
                unobserved.append(ident)
            continue
        entity_slug = _entry_entity_slug(entry)
        if entity_slug:
            joined_aid = entity_to_aid.get(entity_slug)
            if joined_aid and joined_aid in committed:
                continue
        if ident and ident not in committed:
            unobserved.append(str(ident))
    return unobserved


def _observed_unclaimed_keys(
    observed_keys: set[str],
    committed: set[str],
    *,
    call_id_to_aid: dict[str, str],
) -> list[str]:
    unclaimed: list[str] = []
    for key in sorted(observed_keys):
        joined_aid = call_id_to_aid.get(key)
        if joined_aid and joined_aid in committed:
            continue
        if any(key.endswith(aid) for aid in committed):
            continue
        if key in committed:
            continue
        unclaimed.append(key)
    return unclaimed


def reconcile_observed_vs_committed(
    manifest: EffectsManifest | None,
    tool_calls: tuple[ToolCallObservation, ...] | None,
) -> tuple[EffectsManifest | None, list[str]]:
    """Emit divergence in BOTH directions — do not silently resolve (AC-9f)."""
    if manifest is None:
        return None, []
    cortex = manifest.surfaces.get("cortex")
    if cortex is None and not tool_calls:
        return manifest, []

    boundary_index = build_boundary_assertion_index(tool_calls or ())
    entity_to_aid = {
        key: aid for key, aid in boundary_index.items() if not key.startswith("tool_")
    }
    call_id_to_aid = {
        key: aid
        for key, aid in boundary_index.items()
        if key.startswith("tool_") or key.startswith("stream-")
    }
    for key, aid in boundary_index.items():
        call_id_to_aid.setdefault(key, aid)

    committed = _committed_assertion_ids(cortex.entries if cortex else [])
    observed_keys = _observed_cortex_call_keys(tool_calls or ())

    seat_claimed = _seat_claimed_without_ack(
        cortex.entries if cortex else [],
        committed,
        entity_to_aid=entity_to_aid,
    )
    observed_unclaimed = _observed_unclaimed_keys(
        observed_keys,
        committed,
        call_id_to_aid=call_id_to_aid,
    )

    divergences: list[str] = []
    if seat_claimed:
        divergences.extend(
            f"divergence:seat_claimed_unobserved:{ident}" for ident in seat_claimed
        )
    if observed_unclaimed:
        divergences.extend(
            f"divergence:observed_unclaimed:{key}" for key in observed_unclaimed
        )

    if not seat_claimed and not observed_unclaimed:
        return manifest, divergences

    recon = ObservedReconciliation(
        surface="cortex",
        seat_claimed_unobserved=seat_claimed,
        observed_unclaimed=observed_unclaimed,
    )
    existing = list(manifest.reconciliation)
    existing.append(recon)
    updated = manifest.model_copy(update={"reconciliation": existing})
    if cortex is not None and divergences:
        updated_surfaces = dict(updated.surfaces)
        updated_surfaces["cortex"] = cortex.model_copy(
            update={
                "cross_check": cortex.cross_check or "reconcile:observed_vs_committed",
                "authority_class": "observed",
                "absence_semantics": "absence=zero",
            }
        )
        updated = updated.model_copy(update={"surfaces": updated_surfaces})
    return updated, divergences


__all__ = ["reconcile_observed_vs_committed"]

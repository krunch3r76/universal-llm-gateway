"""Observed stream/conversation vs commit-ack reconciliation (item 9 / AC-9f)."""

from __future__ import annotations

import re

from implement_admission.closeout_models import (
    EffectEntry,
    EffectsManifest,
    ObservedReconciliation,
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


def _seat_claimed_without_ack(
    entries: list[EffectEntry],
    committed: set[str],
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
        elif ident and ident not in committed:
            unobserved.append(str(ident))
    return unobserved


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

    committed = _committed_assertion_ids(cortex.entries if cortex else [])
    observed_keys = _observed_cortex_call_keys(tool_calls or ())

    seat_claimed = _seat_claimed_without_ack(
        cortex.entries if cortex else [],
        committed,
    )
    observed_unclaimed = sorted(
        key
        for key in observed_keys
        if not any(key.endswith(aid) for aid in committed)
        and key not in committed
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

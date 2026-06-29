"""Drift detection for agent_skill projections and reference edges."""

from __future__ import annotations

from _skill_constants import _SUPPRESSED
from _skill_projection import (
    _entity_get,
    _expected_declared_related,
    _matches,
    _projection,
)
from _skill_related_sync import list_outgoing_reference_edges, remediation_hint


def _reference_edge_drift(
    client: object,
    slug: str,
    declared: list[str],
    live_edges: list[dict] | None = None,
) -> list[str]:
    eid = f"agent_skill:{slug}"
    if live_edges is None:
        live_edges = list_outgoing_reference_edges(client, slug)
    declared_set = set(declared)
    edge_targets: set[str] = set()
    for row in live_edges:
        target_id = str(row.get("target_id") or "")
        if not target_id.startswith("agent_skill:"):
            continue
        edge_targets.add(target_id.removeprefix("agent_skill:"))
    out: list[str] = []
    for target in sorted(declared_set - edge_targets):
        out.append(
            f"{eid} missing references edge to agent_skill:{target} — "
            f"run: {remediation_hint()}"
        )
    for target in sorted(edge_targets - declared_set):
        out.append(
            f"{eid} stale references edge to agent_skill:{target} "
            f"(not in declared list) — run: {remediation_hint()}"
        )
    return out


def _related_skills_drift(
    client: object,
    slug: str,
    declared: list[str],
    live_by_id: dict[str, dict] | None = None,
) -> str | None:
    eid = f"agent_skill:{slug}"
    if live_by_id is None:
        status, live = _entity_get(client, eid)
        if status == 404:
            return f"{eid} missing from cortex"
        if status != 200:
            return f"{eid} GET {status}"
    else:
        live = live_by_id.get(eid)
        if live is None:
            return f"{eid} missing from cortex"
    if live.get("lifecycle") in _SUPPRESSED:
        return None
    attrs = live.get("attributes") or {}
    live_related = attrs.get("related_skills")
    if sorted(live_related or []) != sorted(declared or []):
        return (
            f"{eid} related_skills live={live_related!r} "
            f"declared={declared!r} — run: {remediation_hint()}"
        )
    return None


def _drifts(
    client: object,
    scanned: dict[str, dict[str, object]],
    live_by_id: dict[str, dict] | None = None,
    *,
    cortex_declared: dict[str, list[str]] | None = None,
) -> list[str]:
    out: list[str] = []
    for slug in sorted(scanned):
        eid = f"agent_skill:{slug}"
        if live_by_id is None:
            status, live = _entity_get(client, eid)
            if status == 404:
                out.append(f"{eid} missing from cortex")
                continue
            if status != 200:
                out.append(f"{eid} GET {status}")
                continue
        else:
            live = live_by_id.get(eid)
            if live is None:
                out.append(f"{eid} missing from cortex")
                continue
        if live.get("lifecycle") in _SUPPRESSED:
            continue
        ok, reason = _matches(live, _projection(scanned[slug], live=live))
        if not ok:
            out.append(f"{eid} {reason}")
        expected = _expected_declared_related(scanned[slug], live)
        if expected is not None:
            out.extend(_reference_edge_drift(client, slug, expected))
    if cortex_declared:
        for slug in sorted(cortex_declared):
            if slug in scanned:
                continue
            drift = _related_skills_drift(
                client, slug, cortex_declared[slug], live_by_id
            )
            if drift:
                out.append(drift)
            out.extend(_reference_edge_drift(client, slug, cortex_declared[slug]))
    return out

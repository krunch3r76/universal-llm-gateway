"""Briefing card section manifest builder."""

from __future__ import annotations

import hashlib
from typing import Any


def build_auto_inject_skills_ref(
    body_md: str,
    injected: list[dict[str, Any]],
    *,
    delivery: str = "web_system_prompt_append",
    ref: str = "mcp_executor._append_web_invariant_bodies",
) -> dict[str, Any]:
    """Compact ref manifest for web/lead auto-inject (body delivered out-of-band)."""
    normalized = body_md.replace("\r\n", "\n").replace("\r", "\n").strip()
    encoded = normalized.encode("utf-8")
    slugs: list[str] = []
    for item in injected:
        entity_id = str(item.get("id") or "").strip()
        if not entity_id:
            continue
        slugs.append(entity_id.split(":", 1)[-1] if ":" in entity_id else entity_id)
    return {
        "inline": False,
        "byte_count": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "skills": slugs,
        "delivery": delivery,
        "ref": ref,
    }


def _sparse_manifest_keep(row: dict[str, Any]) -> bool:
    """Omit manifest rows whose numeric fields are all zero (P4 sparse JSON)."""
    if row.get("items") == []:
        return False
    numeric_keys = (
        "count",
        "unread",
        "plan_phases",
        "in_flight_todos",
        "open_arcs",
        "criticals",
        "warnings",
        "infos",
    )
    present = [row[k] for k in numeric_keys if k in row]
    if present and all(int(v or 0) == 0 for v in present):
        return False
    return True


def build_manifest(
    *,
    plan_phases: list[dict[str, Any]] | None,
    in_flight_todos: list[dict[str, Any]] | None,
    open_arcs: list[dict[str, Any]] | None = None,
    todo_total: int,
    unread_count: int,
    reflective_total: int,
    recent_mentions: list[dict[str, Any]] | None,
    skills: list[dict[str, Any]] | None,
    continuity: dict[str, Any] | None = None,
    views_data: list[dict[str, Any]] | None = None,
    async_dispatches: list[dict[str, Any]] | None = None,
    audit_counters: dict[str, int] | None = None,
    agent: str | None = None,
) -> list[dict[str, Any]]:
    """Build the section manifest returned alongside the briefing card."""
    manifest: list[dict[str, Any]] = []
    if plan_phases or in_flight_todos or open_arcs:
        manifest.append(
            {
                "section": "recent_work",
                "plan_phases": len(plan_phases or []),
                "in_flight_todos": len(in_flight_todos or []),
                "open_arcs": len(open_arcs or []),
                "hint": "GET /boot-recent-work via cortex-api",
            }
        )
    if todo_total > 0:
        manifest.append(
            {
                "section": "todos",
                "count": todo_total,
                "hint": (
                    "cortex(tool='todo_candidates', "
                    'arguments=\'{"query": "<intent>"}\')'
                ),
            }
        )
    manifest.append(
        {
            "section": "sessions",
            "hint": "cortex(tool='journal_read', arguments='{\"limit\": 5}')",
        }
    )
    if continuity:
        manifest.append(
            {
                "section": "continuity",
                "hint": "GET /boot-continuity via cortex-api",
            }
        )
    if unread_count > 0:
        manifest.append(
            {
                "section": "bus",
                "unread": unread_count,
                "hint": 'agent_bus(tool=\'fetch\', arguments=\'{"thread": "480", "last": 10}\')',
            }
        )
    # operational_context is represented as a top-level injected artifact
    # (written_file in LIVE, inline in INSPECT). It MUST NOT also appear here
    # as a manifest_only section — that produced a duplicate in boot_inspect
    # output. The artifact's own `path` field carries the fs hint.
    manifest.append(
        {
            "section": "self_reflections",
            "hint": "cortex(tool='assertions', arguments='{\"entity_id\": \"ai_agent:AGENT\"}')",
        }
    )
    if reflective_total > 0:
        manifest.append(
            {
                "section": "reflective_journal",
                "count": reflective_total,
                "hint": "cortex(tool='rj_list', arguments='{\"limit\": 20}')",
            }
        )
    manifest.append(
        {
            "section": "deadlines",
            "hint": "cortex(tool='deadlines')",
        }
    )
    if recent_mentions:
        manifest.append(
            {
                "section": "recent_mentions",
                "count": len(recent_mentions),
                "hint": (
                    "GET /boot-recent-mentions via cortex-api "
                    "(query params: days, limit, type_exclude)"
                ),
            }
        )
    if skills:
        hint = (
            "Index on briefing_card ## Agent Skills. Browse: "
            'fs(sandbox="cortex", op="md_list", path="agent-skills/"); '
            'read: fs(sandbox="cortex", op="md_read", path="agent-skills/<slug>.md"). '
            "Refresh entity list: cortex(tool='entities', "
            'arguments=\'{"type": "agent_skill", "limit": 500}\' or layer=all via GET /skills. '
            "Agent-skill entity listing returns only lifecycle=active by default; "
            "pass include_non_active=true only for maintenance/debug inspection."
        )
        manifest.append(
            {
                "section": "skills",
                "count": len(skills),
                "hint": hint,
            }
        )
        from agent_seat.body_injection import is_web_seat_slug

        if agent and is_web_seat_slug(agent):
            manifest.append(
                {
                    "section": "skills_index",
                    "count": len(skills),
                    "hint": (
                        f"notes/system/boot/skills-index-{agent}.md "
                        "(LIVE boot writes; see cortex_boot skills_index_ref)"
                    ),
                }
            )

    # §C.4: view materialization entries — one per requested view entity.
    # render_subgraph is the canonical retrieval primitive for these entries.
    for v in views_data or []:
        eid = v.get("entity_id", "")
        if not eid:
            continue
        manifest.append(
            {
                "section": f"views/{eid}",
                "entity_id": eid,
                "entity_count": v.get("entity_count", 0),
                "edge_count": v.get("edge_count", 0),
                "hint": (
                    "cortex(tool='render_subgraph', arguments='"
                    f'{{"root": "{eid}", "hops": 1}}\')'
                ),
            }
        )

    # §C.4: render_subgraph hint for plan/project entities surfaced via plan_phases.
    # Enables agents to drill into a roadmap subgraph from the manifest entry.
    _seen_plan_ids: set[str] = set()
    for phase in plan_phases or []:
        plan_id = phase.get("plan_id") or ""
        if plan_id and plan_id not in _seen_plan_ids:
            _seen_plan_ids.add(plan_id)
            manifest.append(
                {
                    "section": f"subgraph/{plan_id}",
                    "entity_id": plan_id,
                    "hint": (
                        "cortex(tool='render_subgraph', arguments='"
                        f'{{"root": "{plan_id}", "hops": 1}}\')'
                    ),
                }
            )

    # §C.6: in-flight async dispatch section — structural IDs + retrieval hints.
    if async_dispatches:
        manifest.append(
            {
                "section": "async_dispatches",
                "count": len(async_dispatches),
                "hint": "pipeline(op='stats') for aggregate; pipeline(op='result', execution_id=<id>) per dispatch",
                "items": [
                    {
                        "execution_id": d.get("execution_id", ""),
                        "pipeline_id": d.get("pipeline_id", ""),
                    }
                    for d in async_dispatches
                ],
            }
        )

    # §C.6: audit alert counts — degrade silently when audit unavailable.
    if audit_counters is not None:
        manifest.append(
            {
                "section": "audit",
                "criticals": audit_counters.get("criticals", 0),
                "warnings": audit_counters.get("warnings", 0),
                "infos": audit_counters.get("infos", 0),
                "hint": "cortex(tool='audit') for findings detail",
            }
        )

    return [row for row in manifest if _sparse_manifest_keep(row)]

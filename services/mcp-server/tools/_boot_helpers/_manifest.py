"""Briefing card section manifest builder."""

from __future__ import annotations

from typing import Any


def _build_manifest(
    *,
    plan_phases: list[dict[str, Any]] | None,
    in_flight_todos: list[dict[str, Any]] | None,
    todo_total: int,
    unread_count: int,
    op_ctx_path: str,
    reflective_total: int,
    recent_mentions: list[dict[str, Any]] | None,
    skills: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Build the section manifest returned alongside the briefing card."""
    manifest: list[dict[str, Any]] = []
    if plan_phases or in_flight_todos:
        manifest.append(
            {
                "section": "recent_work",
                "plan_phases": len(plan_phases or []),
                "in_flight_todos": len(in_flight_todos or []),
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
    if unread_count > 0:
        manifest.append(
            {
                "section": "bus",
                "unread": unread_count,
                "hint": 'agent_bus(tool=\'fetch\', arguments=\'{"thread": "480", "last": 10}\')',
            }
        )
    if op_ctx_path:
        manifest.append(
            {
                "section": "operational_context",
                "hint": f"fs(sandbox='cortex', op='md_list', path='{op_ctx_path}')",
            }
        )
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
        manifest.append(
            {
                "section": "skills",
                "count": len(skills),
                "hint": (
                    "cortex(tool='entities', "
                    'arguments=\'{"type": "agent_skill"}\') — '
                    "then fs(sandbox='cortex', op='read', path=<source_uri>) "
                    "for the full SKILL.md"
                ),
            }
        )
    return manifest

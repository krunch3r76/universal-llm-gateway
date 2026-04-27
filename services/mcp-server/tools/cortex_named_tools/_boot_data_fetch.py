"""Parallel data fetch and result extraction for boot briefing."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from .._boot_helpers import safe_list
from .._cortex_relay import _cx
from .._local_relay import relay as _relay
from ._boot_profiles import _BOOT_PROFILES


def _build_futures_spec(
    agent: str,
    profile: dict[str, Any],
) -> dict[str, tuple[Any, ...]]:
    """Build the parallel-fetch spec for a boot briefing."""
    unread_turns_qs = urlencode(
        {"to": agent, "unread": "true", "last": 10, "compact": "true"}
    )
    session_qs_parts: dict[str, str | int] = {"limit": profile.get("session_limit", 3)}
    if profile.get("session_agent_filter"):
        session_qs_parts["agent"] = profile["session_agent_filter"]
    session_qs = urlencode(session_qs_parts)

    futures_spec: dict[str, tuple[Any, ...]] = {
        "sessions": (_cx, "GET", f"/session-journals?{session_qs}"),
        "threads": (_relay, "agent-bus", "GET", "/threads?status=active"),
        "unread_turns": (_relay, "agent-bus", "GET", f"/turns?{unread_turns_qs}"),
    }

    if profile.get("include_deadlines", True):
        futures_spec["deadlines"] = (_cx, "GET", "/deadlines")
    if profile.get("include_review_queue", True):
        futures_spec["staging"] = (_cx, "GET", "/staging?status=pending&limit=5")

    todo_qs_parts: dict[str, Any] = {"limit": 15}
    if agent == "web":
        todo_qs_parts["domain_exclude"] = "infra,rag,pipeline,mcp,model_id"
    futures_spec["todos"] = (_cx, "GET", f"/boot-todos?{urlencode(todo_qs_parts)}")
    futures_spec["temporal"] = (_cx, "GET", "/boot-temporal")

    rj_agent = {"cursor": "cursor-claude", "web": "web-claude"}.get(agent, agent)
    futures_spec["reflective_journal"] = (
        _cx, "GET", f"/boot-reflective?{urlencode({'agent': rj_agent, 'limit': 5})}"
    )

    futures_spec["recent_mentions"] = (
        _cx, "GET", f"/boot-recent-mentions?{urlencode({'days': 7, 'limit': 10})}"
    )
    futures_spec["skills"] = (
        _cx, "GET", f"/entities?{urlencode({'type': 'agent_skill', 'limit': 50})}"
    )
    futures_spec["recent_work"] = (_cx, "GET", "/boot-recent-work")

    self_entity_id = profile.get("self_entity_id")
    self_reflections_limit = profile.get("self_reflections_limit", 0)
    if self_entity_id and self_reflections_limit > 0:
        refl_qs = urlencode(
            {
                "entity_id": self_entity_id,
                "superseded": "false",
                "limit": self_reflections_limit,
            }
        )
        futures_spec["self_reflections"] = (_cx, "GET", f"/assertions?{refl_qs}")

    return futures_spec


def _extract_boot_results(
    agent: str,
    raw: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
    """Unpack the raw parallel-fetch results into typed lists."""
    from .._boot_helpers import filter_stale_open_items

    sessions: list[dict[str, Any]] = safe_list(raw["sessions"])
    deadlines: list[dict[str, Any]] = safe_list(raw.get("deadlines", []))
    threads: list[dict[str, Any]] = safe_list(raw["threads"], "threads")
    unread_turns: list[dict[str, Any]] = safe_list(raw["unread_turns"], "turns")
    staging_items: list[dict[str, Any]] = safe_list(raw.get("staging", []))
    todos: list[dict[str, Any]] = safe_list(raw.get("todos", []))
    self_reflections: list[dict[str, Any]] = safe_list(raw.get("self_reflections", []))
    rj_entries: list[dict[str, Any]] = safe_list(raw.get("reflective_journal", []))
    rj_raw = raw.get("reflective_journal", {})
    rj_total: int = rj_raw.get("total", 0) if isinstance(rj_raw, dict) else 0

    recent_mentions: list[dict[str, Any]] = safe_list(raw.get("recent_mentions", []))
    skills: list[dict[str, Any]] = safe_list(raw.get("skills", []))

    recent_work_raw = raw.get("recent_work", {})
    plan_phases: list[dict[str, Any]] = (
        recent_work_raw.get("plan_phases", []) if isinstance(recent_work_raw, dict) else []
    )
    in_flight_todos: list[dict[str, Any]] = (
        recent_work_raw.get("in_flight_todos", []) if isinstance(recent_work_raw, dict) else []
    )

    if agent == "web":
        _web_domain_exclude = {"infra", "rag", "pipeline", "mcp", "model_id"}
        todos = [t for t in todos if t.get("domain") not in _web_domain_exclude]

    temporal_raw = raw.get("temporal", {})
    temporal_active: list[dict[str, Any]] = safe_list(
        temporal_raw.get("active", []) if isinstance(temporal_raw, dict) else []
    )
    temporal_recently_resolved: list[dict[str, Any]] = safe_list(
        temporal_raw.get("recently_resolved", []) if isinstance(temporal_raw, dict) else []
    )
    expired_unresolved: list[dict[str, Any]] = safe_list(
        temporal_raw.get("expired_unresolved", []) if isinstance(temporal_raw, dict) else []
    )
    sessions = filter_stale_open_items(sessions, temporal_recently_resolved)

    review_total: int | None = None
    if profile.get("include_review_queue", True):
        review_total = len(staging_items)

    return {
        "sessions": sessions,
        "deadlines": deadlines,
        "threads": threads,
        "unread_turns": unread_turns,
        "staging_items": staging_items,
        "todos": todos,
        "self_reflections": self_reflections,
        "rj_entries": rj_entries,
        "rj_total": rj_total,
        "recent_mentions": recent_mentions,
        "skills": skills,
        "plan_phases": plan_phases,
        "in_flight_todos": in_flight_todos,
        "temporal_active": temporal_active,
        "expired_unresolved": expired_unresolved,
        "review_total": review_total,
    }

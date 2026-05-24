"""Parallel data fetch and result extraction for boot briefing."""

from __future__ import annotations

import os
import time
from typing import Any
from urllib.parse import urlencode

import httpx
from transport_utils import make_sync_client
from universal_logging import get_logger

from .._boot_helpers import safe_list
from .._cortex_relay import cx as cortex_cx
from .._local_relay import relay as _relay

logger = get_logger(__name__)

_EVENTS_QUERY_SOCKET = os.environ.get(
    "EVENTS_QUERY_SOCK", "/tmp/universal-protocol/events-query.sock"
)
_24H_MS = 24 * 60 * 60 * 1000


def _fetch_async_dispatches_from_events(agent: str) -> list[dict[str, Any]]:
    """Query event service for async pipeline dispatches in-flight for `agent`.

    In-flight = has a ``pipeline.dispatch.async`` event but NO corresponding
    ``pipeline.dispatch.completed`` event in the last 24 h.  Returns an empty
    list on any failure (graceful degradation — boot must not block).

    ∀ returned entry: {execution_id, pipeline_id, started_at, retrieval_hint}.
    No prose, no model output — structural IDs only (§C.3).
    """
    cutoff_ms = int(time.time() * 1000) - _24H_MS
    sql = (
        "SELECT e.execution_id,"
        " json_extract(e.payload, '$.pipeline_id') AS pipeline_id,"
        " e.timestamp AS started_at"
        " FROM events e"
        " WHERE e.signal = 'pipeline.dispatch.async'"
        "   AND json_extract(e.payload, '$.caller_agent') = ?"
        "   AND e.ts_unix_ms > ?"
        "   AND NOT EXISTS ("
        "     SELECT 1 FROM events c"
        "     WHERE c.signal = 'pipeline.dispatch.completed'"
        "       AND c.execution_id = e.execution_id"
        "   )"
        " ORDER BY e.ts_unix_ms DESC"
        " LIMIT 10"
    )
    try:
        with make_sync_client(f"unix://{_EVENTS_QUERY_SOCKET}", timeout=5.0) as client:
            resp = client.post(
                "/v1/query",
                json={"type": "sql", "sql": sql, "params": [agent, cutoff_ms]},
            )
            resp.raise_for_status()
            data = resp.json()
    except (httpx.RequestError, httpx.HTTPError, ValueError) as exc:
        logger.debug("async dispatch event query failed: %s", exc)
        return []

    dispatches: list[dict[str, Any]] = []
    for row in data.get("rows", []):
        eid = row.get("execution_id") or ""
        pid = row.get("pipeline_id") or ""
        if not eid:
            continue
        dispatches.append(
            {
                "execution_id": eid,
                "pipeline_id": pid,
                "started_at": row.get("started_at", ""),
                "status": "running",
                "retrieval_hint": (f"pipeline(op='result', execution_id='{eid}')"),
            }
        )
    return dispatches


def build_futures_spec(
    agent: str,
    profile: dict[str, Any],
    recorder: Any,
) -> dict[str, tuple[Any, ...]]:
    """Build the parallel-fetch spec for a boot briefing.

    `recorder` is a FetchRecorder whose .wrap() method proxies each callable
    to capture provenance. Imported via Any to avoid a circular import with
    _boot_manifest (which lives in the same package).
    """
    wrapped_cx = recorder.wrap("cortex", cortex_cx)
    relay = recorder.wrap("agent-bus", _relay)

    unread_turns_qs = urlencode(
        {"to": agent, "unread": "true", "last": 10, "compact": "true"}
    )
    # Boot card displays at most 10 unread threads (briefing card §
    # "Agent Bus — N unread"). The full active-thread set runs into the
    # hundreds; ask the API for the projection we render and stop paying
    # ~60 KB UDS per boot to filter client-side.
    threads_qs = urlencode({"status": "active", "has_unread": "true", "limit": 10})
    session_qs_parts: dict[str, str | int] = {"limit": profile.get("session_limit", 3)}
    if profile.get("session_agent_filter"):
        session_qs_parts["agent"] = profile["session_agent_filter"]
    session_qs = urlencode(session_qs_parts)

    # Read-only fetch-graph audit (blocking contract for BootMode.INSPECT):
    #
    # - sessions: GET /session-journals?...            -> list-only read
    # - continuity: GET /boot-continuity?agent=...     -> list-only read
    # - threads: GET /threads?status=active            -> list-only read
    # - unread_turns: GET /turns?...                   -> read-only (no mark_read)
    # - deadlines: GET /deadlines                      -> list-only read
    # - staging: GET /staging?status=pending&limit=5   -> list-only read
    # - todos: GET /boot-todos?...                     -> list-only read
    # - temporal: GET /boot-temporal                   -> list-only read
    # - reflective_journal: GET /boot-reflective?...   -> list-only read
    # - recent_mentions: GET /boot-recent-mentions?... -> list-only read
    # - skills: GET /entities?type=agent_skill...      -> list-only read
    # - recent_work: GET /boot-recent-work             -> list-only read
    # - self_reflections: GET /assertions?...          -> list-only read
    #
    # Any mutating fetch in this graph is a hard blocker for INSPECT mode.
    futures_spec: dict[str, tuple[Any, ...]] = {
        # read-only: list recent session journals for boot continuity
        "sessions": (wrapped_cx, "GET", f"/session-journals?{session_qs}"),
        # read-only: fetch last-session handoff + continuity chain
        "continuity": (
            wrapped_cx,
            "GET",
            f"/boot-continuity?{urlencode({'agent': agent})}",
        ),
        # read-only: compact attention list — has_unread=true&limit=10
        "threads": (relay, "agent-bus", "GET", f"/threads?{threads_qs}"),
        # read-only: unread lookup only; query does not include mark_read
        "unread_turns": (relay, "agent-bus", "GET", f"/turns?{unread_turns_qs}"),
    }

    if profile.get("include_deadlines", True):
        # read-only: fetch pending deadline list
        futures_spec["deadlines"] = (wrapped_cx, "GET", "/deadlines")
    if profile.get("include_review_queue", True):
        # read-only: fetch pending staging queue slice
        futures_spec["staging"] = (wrapped_cx, "GET", "/staging?status=pending&limit=5")

    # Briefing card renders at most 5 todos and a count — ship the compact
    # projection (id, title, priority, domain) instead of the full description /
    # source_uri payload that the renderer drops.
    todo_qs_parts: dict[str, Any] = {"limit": 15, "compact": "true"}
    _seat_parts = agent.split("-", 1)
    if len(_seat_parts) == 2 and _seat_parts[1] == "web":
        todo_qs_parts["domain_exclude"] = "infra,rag,pipeline,mcp,model_id"
    # read-only: fetch todo index for briefing card prioritization
    futures_spec["todos"] = (
        wrapped_cx,
        "GET",
        f"/boot-todos?{urlencode(todo_qs_parts)}",
    )
    # read-only: fetch active/expired temporal assertions; briefing renders
    # only [:5] of each bucket — pass per-bucket limits so the API does the
    # slicing and we stop shipping 4×10 rows when we use 4×5.
    temporal_qs = urlencode(
        {
            "active_limit": 5,
            "upcoming_limit": 5,
            "expired_limit": 5,
            "resolved_limit": 10,
        }
    )
    futures_spec["temporal"] = (wrapped_cx, "GET", f"/boot-temporal?{temporal_qs}")

    # Phase 7: the reflective journal agent key is now the family slug
    # derived from the seat slug ({family}-{platform} → family part).
    # Falls back to the full seat slug for unknown formats.
    _parts = agent.split("-", 1)
    rj_agent = (
        _parts[0]
        if len(_parts) == 2
        and _parts[0] in {"claude", "gpt", "grok", "gemini", "subagent"}
        else agent
    )
    # read-only: fetch reflective journal entries for agent continuity
    futures_spec["reflective_journal"] = (
        wrapped_cx,
        "GET",
        f"/boot-reflective?{urlencode({'agent': rj_agent, 'limit': 5})}",
    )

    # read-only: fetch recent mentions for salience rendering
    futures_spec["recent_mentions"] = (
        wrapped_cx,
        "GET",
        f"/boot-recent-mentions?{urlencode({'days': 7, 'limit': 10})}",
    )
    # read-only: compact skill projection (id, name, description_first_sentence)
    # via dedicated /boot-skills endpoint. The wider /entities surface ships
    # full description bodies the renderer drops anyway. `for_agent` filters
    # to skills whose `applicable_agents` attribute contains `*` (universal)
    # or this agent slug — pre-backfill skills without the attribute are
    # treated as universal so the filter is non-narrowing until each entity
    # opts in via `entity_update`.
    futures_spec["skills"] = (
        wrapped_cx,
        "GET",
        f"/boot-skills?{urlencode({'limit': 50, 'for_agent': agent})}",
    )
    # read-only: fetch recent plan/todo activity summary
    futures_spec["recent_work"] = (wrapped_cx, "GET", "/boot-recent-work")
    # read-only: graph-only audit (no filesystem) — surfaces critical alert counts
    futures_spec["audit"] = (
        wrapped_cx,
        "POST",
        "/dispatch",
        {"tool": "audit", "arguments": {}},
    )
    # read-only: in-flight async dispatches for this agent from event service
    futures_spec["async_dispatches"] = (
        _fetch_async_dispatches_from_events,
        agent,
    )

    self_entity_id = profile.get("self_entity_id")
    self_reflections_limit = profile.get("self_reflections_limit", 0)
    if self_entity_id and self_reflections_limit > 0:
        # §boot-compact: briefing renders only id/claim/observed_at/seeded_by
        # /derivation_type/entity_id on the "Your Notes" section. Full-payload
        # cost was ~11.2 KB per boot (ship-vs-render ratio ~28× for web);
        # compact projection drops it to ~1–2 KB. Thread 882 turn 12 contract.
        refl_qs = urlencode(
            {
                "entity_id": self_entity_id,
                "superseded": "false",
                "limit": self_reflections_limit,
                "compact": "true",
            }
        )
        # read-only: fetch non-superseded self-reflection assertions
        futures_spec["self_reflections"] = (wrapped_cx, "GET", f"/assertions?{refl_qs}")

    return futures_spec


def extract_boot_results(
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
    # `/boot-skills` ships a sibling `unpartitioned_count` (skills missing
    # `applicable_agents`). Surfaced on the briefing card as a drift reminder
    # so the partition script doesn't go stale silently.
    skills_raw = raw.get("skills", {})
    skills_unpartitioned: int = (
        int(skills_raw.get("unpartitioned_count", 0) or 0)
        if isinstance(skills_raw, dict)
        else 0
    )

    recent_work_raw = raw.get("recent_work", {})
    plan_phases: list[dict[str, Any]] = (
        recent_work_raw.get("plan_phases", [])
        if isinstance(recent_work_raw, dict)
        else []
    )
    in_flight_todos: list[dict[str, Any]] = (
        recent_work_raw.get("in_flight_todos", [])
        if isinstance(recent_work_raw, dict)
        else []
    )

    # Web seats (claude-web, grok-web) get a domain-filtered todo list —
    # operator-facing role; infra/pipeline noise is irrelevant.
    _agent_parts = agent.split("-", 1)
    _agent_platform = _agent_parts[1] if len(_agent_parts) == 2 else ""
    if _agent_platform == "web":
        _web_domain_exclude = {"infra", "rag", "pipeline", "mcp", "model_id"}
        todos = [t for t in todos if t.get("domain") not in _web_domain_exclude]

    temporal_raw = raw.get("temporal", {})
    temporal_active: list[dict[str, Any]] = safe_list(
        temporal_raw.get("active", []) if isinstance(temporal_raw, dict) else []
    )
    temporal_recently_resolved: list[dict[str, Any]] = safe_list(
        temporal_raw.get("recently_resolved", [])
        if isinstance(temporal_raw, dict)
        else []
    )
    expired_unresolved: list[dict[str, Any]] = safe_list(
        temporal_raw.get("expired_unresolved", [])
        if isinstance(temporal_raw, dict)
        else []
    )
    sessions = filter_stale_open_items(sessions, temporal_recently_resolved)

    review_total: int | None = None
    if profile.get("include_review_queue", True):
        review_total = len(staging_items)

    # Audit — extract severity counters; degrade gracefully if unavailable.
    _audit_raw = raw.get("audit", {})
    audit_counters: dict[str, int] | None = None
    if isinstance(_audit_raw, dict) and "criticals" in _audit_raw:
        audit_counters = {
            "criticals": int(_audit_raw.get("criticals", 0)),
            "warnings": int(_audit_raw.get("warnings", 0)),
            "infos": int(_audit_raw.get("infos", 0)),
        }

    # In-flight async dispatches — already a structured list from event service.
    async_dispatches: list[dict[str, Any]] = safe_list(raw.get("async_dispatches", []))

    return {
        "sessions": sessions,
        "continuity": raw.get("continuity")
        if isinstance(raw.get("continuity"), dict)
        else {},
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
        "skills_unpartitioned_count": skills_unpartitioned,
        "plan_phases": plan_phases,
        "in_flight_todos": in_flight_todos,
        "temporal_active": temporal_active,
        "expired_unresolved": expired_unresolved,
        "review_total": review_total,
        "audit_counters": audit_counters,
        "async_dispatches": async_dispatches,
    }

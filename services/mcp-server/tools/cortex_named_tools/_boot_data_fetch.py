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
    except (httpx.HTTPError, ValueError) as exc:
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

    unread_toc_qs = urlencode(
        {"to": agent, "active_since": "14d", "limit": 10}
    )
    # Boot card uses windowed unread-toc sample + unwindowed totals (Q6).
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
    # - unread_toc: GET /turns/unread-toc?...              -> read-only digest
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
        # read-only: windowed unread digest with unwindowed totals (E1/Q6)
        "unread_toc": (relay, "agent-bus", "GET", f"/turns/unread-toc?{unread_toc_qs}"),
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
    from ._boot_domain import extend_todo_fetch_params, normalize_boot_domain

    extend_todo_fetch_params(
        agent,
        todo_qs_parts,
        domain=normalize_boot_domain(profile.get("domain")),
    )
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

    # Reflective journal is seat-keyed (e.g. `claude-web`, `grok-cursor`,
    # `claude-cursor`) — pass the full seat slug. Stripping to the family
    # slug returned 0 rows for every seat in the current data set
    # (reflective-journal seat-lookup discovery, claude-web-2026-05-24-0754).
    # Cross-seat family-register lookup still works for callers that pass
    # `agent=<family>` directly.
    futures_spec["reflective_journal"] = (
        wrapped_cx,
        "GET",
        f"/boot-reflective?{urlencode({'agent': agent, 'limit': 5})}",
    )

    # read-only: fetch recent mentions for salience rendering
    futures_spec["recent_mentions"] = (
        wrapped_cx,
        "GET",
        f"/boot-recent-mentions?{urlencode({'days': 7, 'limit': 10})}",
    )
    # read-only: boot skill projection via canonical GET /skills?view=boot.
    # render=concise,card ships server-side sidecar + card section markdown.
    futures_spec["skills"] = (
        wrapped_cx,
        "GET",
        f"/skills?{urlencode({'limit': 120, 'for_agent': agent, 'view': 'boot', 'render': 'concise,card'})}",
    )
    # read-only: fetch recent plan/todo activity summary
    futures_spec["recent_work"] = (wrapped_cx, "GET", "/boot-recent-work")
    # read-only: severity counts only — ¬full audit findings payload (~MB-scale)
    futures_spec["audit"] = (wrapped_cx, "GET", "/boot-audit-counters")
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

    principal = profile.get("principal")
    if principal:
        principal_qs = urlencode(
            {
                "principal": principal,
                "active_limit": profile.get("principal_active_limit", 5),
            }
        )
        futures_spec["principal_context"] = (
            wrapped_cx,
            "GET",
            f"/boot-principal-context?{principal_qs}",
        )

    return futures_spec


def extract_boot_results(
    agent: str,
    raw: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
    """Unpack the raw parallel-fetch results into typed lists."""
    from .._boot_helpers import filter_stale_open_items
    from ._boot_domain import apply_domain_todo_state, normalize_boot_domain

    boot_domain = normalize_boot_domain(profile.get("domain"))

    sessions: list[dict[str, Any]] = safe_list(raw["sessions"])
    deadlines: list[dict[str, Any]] = safe_list(raw.get("deadlines", []))
    threads: list[dict[str, Any]] = safe_list(raw["threads"], "threads")
    unread_toc_raw = raw.get("unread_toc", {})
    unread_toc_threads: list[dict[str, Any]] = (
        safe_list(unread_toc_raw.get("threads", []), "threads")
        if isinstance(unread_toc_raw, dict)
        else []
    )
    unread_thread_total = (
        int(unread_toc_raw.get("total_unread_threads", 0) or 0)
        if isinstance(unread_toc_raw, dict)
        else 0
    )
    unread_turn_total = (
        int(unread_toc_raw.get("total_unread_turns", 0) or 0)
        if isinstance(unread_toc_raw, dict)
        else 0
    )
    unread_window_label = (
        str(unread_toc_raw.get("active_since") or "14d window")
        if isinstance(unread_toc_raw, dict)
        else "14d window"
    )
    staging_items: list[dict[str, Any]] = safe_list(raw.get("staging", []))
    todos: list[dict[str, Any]] = safe_list(raw.get("todos", []))
    self_reflections: list[dict[str, Any]] = safe_list(raw.get("self_reflections", []))
    rj_entries: list[dict[str, Any]] = safe_list(raw.get("reflective_journal", []))
    rj_raw = raw.get("reflective_journal", {})
    rj_total: int = rj_raw.get("total", 0) if isinstance(rj_raw, dict) else 0

    recent_mentions: list[dict[str, Any]] = safe_list(raw.get("recent_mentions", []))
    skills: list[dict[str, Any]] = safe_list(raw.get("skills", []))
    # GET /skills?view=boot ships `unpartitioned_count` (skills missing
    # `applicable_agents`). Surfaced on the briefing card as a drift reminder
    # so the partition script doesn't go stale silently.
    skills_raw = raw.get("skills", {})
    skills_unpartitioned: int = (
        int(skills_raw.get("unpartitioned_count", 0) or 0)
        if isinstance(skills_raw, dict)
        else 0
    )
    skills_concise_markdown: str | None = None
    skills_card_markdown: str | None = None
    if isinstance(skills_raw, dict):
        rendered = skills_raw.get("rendered")
        if isinstance(rendered, dict):
            concise = rendered.get("concise_markdown")
            if isinstance(concise, str) and concise.strip():
                skills_concise_markdown = concise
            card = rendered.get("card_markdown")
            if isinstance(card, str) and card.strip():
                skills_card_markdown = card

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
    open_arcs: list[dict[str, Any]] = (
        recent_work_raw.get("open_arcs", [])
        if isinstance(recent_work_raw, dict)
        else []
    )

    todos, cross_domain_sentinel = apply_domain_todo_state(
        todos,
        domain=boot_domain,
        agent=agent,
        deadlines=deadlines,
    )

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

    # Audit — extract severity counters; omit section when unavailable.
    _audit_raw = raw.get("audit", {})
    audit_counters: dict[str, int] | None = None
    if (
        isinstance(_audit_raw, dict)
        and not _audit_raw.get("unavailable")
        and "criticals" in _audit_raw
    ):
        audit_counters = {
            "criticals": int(_audit_raw.get("criticals", 0)),
            "warnings": int(_audit_raw.get("warnings", 0)),
            "infos": int(_audit_raw.get("infos", 0)),
        }

    # In-flight async dispatches — already a structured list from event service.
    async_dispatches: list[dict[str, Any]] = safe_list(raw.get("async_dispatches", []))

    principal_context: dict[str, Any] | None = None
    _pc_raw = raw.get("principal_context")
    if isinstance(_pc_raw, dict) and _pc_raw.get("principal_id"):
        principal_context = _pc_raw

    return {
        "sessions": sessions,
        "continuity": raw.get("continuity")
        if isinstance(raw.get("continuity"), dict)
        else {},
        "deadlines": deadlines,
        "threads": threads,
        "unread_toc_threads": unread_toc_threads,
        "unread_thread_total": unread_thread_total,
        "unread_turn_total": unread_turn_total,
        "unread_window_label": unread_window_label,
        "staging_items": staging_items,
        "todos": todos,
        "self_reflections": self_reflections,
        "rj_entries": rj_entries,
        "rj_total": rj_total,
        "recent_mentions": recent_mentions,
        "skills": skills,
        "skills_unpartitioned_count": skills_unpartitioned,
        "skills_concise_markdown": skills_concise_markdown,
        "skills_card_markdown": skills_card_markdown,
        "plan_phases": plan_phases,
        "in_flight_todos": in_flight_todos,
        "open_arcs": open_arcs,
        "temporal_active": temporal_active,
        "expired_unresolved": expired_unresolved,
        "review_total": review_total,
        "audit_counters": audit_counters,
        "async_dispatches": async_dispatches,
        "principal_context": principal_context,
        "cross_domain_sentinel": cross_domain_sentinel,
        "boot_domain": boot_domain,
    }

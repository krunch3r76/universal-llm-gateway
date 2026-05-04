"""Parallel data fetch and result extraction for boot briefing."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlencode

import httpx

from .._boot_helpers import safe_list
from .._cortex_relay import _cx
from .._local_relay import relay as _relay

_STARGATE_URL = os.environ.get("STARGATE_URL", "http://io:9999")


def _fetch_rag_pipeline_state(stargate_url: str) -> dict[str, Any]:
    """Fetch RAG pipeline stall indicators for the boot briefing.

    Synchronous — submitted to the boot ThreadPoolExecutor alongside other relay
    callables. Uses httpx.Client (sync) so the coroutine is not submitted to a
    thread pool unawaited. Timeout is 2s to bound boot-path latency.

    Returns {"unreachable": True} on total failure; partial failures return
    whatever data was successfully fetched (pending/stale may both be 0 on
    partial failure, which is acceptable — boot should not fail because RAG is down).
    """
    from mcp_events import record as _record

    pending = 0
    failures = 0
    stale_vocab = 0
    auth_token = os.environ.get("MCP_AUTH_TOKEN", "")
    headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}
    try:
        with httpx.Client(timeout=2.0, headers=headers) as client:
            try:
                queue_resp = client.get(f"{stargate_url}/api/v1/rag/extraction/queue")
                if queue_resp.status_code == 200:
                    data = queue_resp.json()
                    pending = data.get("breakdown", {}).get("total", 0)
                else:
                    _record(
                        "mcp.rag.boot.fetch.failed",
                        endpoint="extraction/queue",
                        error=f"HTTP {queue_resp.status_code}",
                    )
            except Exception as exc:
                _record(
                    "mcp.rag.boot.fetch.failed",
                    endpoint="extraction/queue",
                    error=str(exc),
                )

            try:
                status_resp = client.get(f"{stargate_url}/api/v1/rag/indexing/status")
                if status_resp.status_code == 200:
                    data = status_resp.json()
                    failures = data.get(
                        "indexing_failures_permanent_count", 0
                    ) + data.get("indexing_failures_transient_count", 0)
                    stale_vocab = data.get("stale_corpus_hints_count", 0)
                else:
                    _record(
                        "mcp.rag.boot.fetch.failed",
                        endpoint="indexing/status",
                        error=f"HTTP {status_resp.status_code}",
                    )
            except Exception as exc:
                _record(
                    "mcp.rag.boot.fetch.failed",
                    endpoint="indexing/status",
                    error=str(exc),
                )

        return {
            "pending_contextualization": pending,
            "indexing_failures": failures,
            "stale_corpus_hints": stale_vocab,
        }
    except Exception as exc:
        _record("mcp.rag.boot.fetch.failed", endpoint="all", error=str(exc))
        return {"unreachable": True}


def _build_futures_spec(
    agent: str,
    profile: dict[str, Any],
    recorder: Any,
) -> dict[str, tuple[Any, ...]]:
    """Build the parallel-fetch spec for a boot briefing.

    `recorder` is a FetchRecorder whose .wrap() method proxies each callable
    to capture provenance. Imported via Any to avoid a circular import with
    _boot_manifest (which lives in the same package).
    """
    cx = recorder.wrap("cortex", _cx)
    relay = recorder.wrap("agent-bus", _relay)
    rag = recorder.wrap("stargate", _fetch_rag_pipeline_state)

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
    # - rag_pipeline: GET /api/v1/rag/* status routes  -> list-only read
    # - self_reflections: GET /assertions?...          -> list-only read
    #
    # Any mutating fetch in this graph is a hard blocker for INSPECT mode.
    futures_spec: dict[str, tuple[Any, ...]] = {
        # read-only: list recent session journals for boot continuity
        "sessions": (cx, "GET", f"/session-journals?{session_qs}"),
        # read-only: compact attention list — has_unread=true&limit=10
        "threads": (relay, "agent-bus", "GET", f"/threads?{threads_qs}"),
        # read-only: unread lookup only; query does not include mark_read
        "unread_turns": (relay, "agent-bus", "GET", f"/turns?{unread_turns_qs}"),
    }

    if profile.get("include_deadlines", True):
        # read-only: fetch pending deadline list
        futures_spec["deadlines"] = (cx, "GET", "/deadlines")
    if profile.get("include_review_queue", True):
        # read-only: fetch pending staging queue slice
        futures_spec["staging"] = (cx, "GET", "/staging?status=pending&limit=5")

    # Briefing card renders at most 5 todos and a count — ship the compact
    # projection (id, title, priority, domain) instead of the full description /
    # source_uri payload that the renderer drops.
    todo_qs_parts: dict[str, Any] = {"limit": 15, "compact": "true"}
    if agent == "web":
        todo_qs_parts["domain_exclude"] = "infra,rag,pipeline,mcp,model_id"
    # read-only: fetch todo index for briefing card prioritization
    futures_spec["todos"] = (cx, "GET", f"/boot-todos?{urlencode(todo_qs_parts)}")
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
    futures_spec["temporal"] = (cx, "GET", f"/boot-temporal?{temporal_qs}")

    rj_agent = {"cursor": "cursor-claude", "web": "web-claude"}.get(agent, agent)
    # read-only: fetch reflective journal entries for agent continuity
    futures_spec["reflective_journal"] = (
        cx,
        "GET",
        f"/boot-reflective?{urlencode({'agent': rj_agent, 'limit': 5})}",
    )

    # read-only: fetch recent mentions for salience rendering
    futures_spec["recent_mentions"] = (
        cx,
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
        cx,
        "GET",
        f"/boot-skills?{urlencode({'limit': 50, 'for_agent': agent})}",
    )
    # read-only: fetch recent plan/todo activity summary
    futures_spec["recent_work"] = (cx, "GET", "/boot-recent-work")
    # read-only: fetch RAG status endpoints (queue + indexing status)
    futures_spec["rag_pipeline"] = (rag, _STARGATE_URL)

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
        futures_spec["self_reflections"] = (cx, "GET", f"/assertions?{refl_qs}")

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

    rag_pipeline_raw = raw.get("rag_pipeline")
    rag_pipeline: dict[str, Any] = (
        rag_pipeline_raw if isinstance(rag_pipeline_raw, dict) else {}
    )

    if agent == "web":
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
        "skills_unpartitioned_count": skills_unpartitioned,
        "plan_phases": plan_phases,
        "in_flight_todos": in_flight_todos,
        "temporal_active": temporal_active,
        "expired_unresolved": expired_unresolved,
        "review_total": review_total,
        "rag_pipeline": rag_pipeline,
    }

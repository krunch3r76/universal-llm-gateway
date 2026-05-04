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
    session_qs_parts: dict[str, str | int] = {"limit": profile.get("session_limit", 3)}
    if profile.get("session_agent_filter"):
        session_qs_parts["agent"] = profile["session_agent_filter"]
    session_qs = urlencode(session_qs_parts)

    futures_spec: dict[str, tuple[Any, ...]] = {
        "sessions": (cx, "GET", f"/session-journals?{session_qs}"),
        "threads": (relay, "agent-bus", "GET", "/threads?status=active"),
        "unread_turns": (relay, "agent-bus", "GET", f"/turns?{unread_turns_qs}"),
    }

    if profile.get("include_deadlines", True):
        futures_spec["deadlines"] = (cx, "GET", "/deadlines")
    if profile.get("include_review_queue", True):
        futures_spec["staging"] = (cx, "GET", "/staging?status=pending&limit=5")

    todo_qs_parts: dict[str, Any] = {"limit": 15}
    if agent == "web":
        todo_qs_parts["domain_exclude"] = "infra,rag,pipeline,mcp,model_id"
    futures_spec["todos"] = (cx, "GET", f"/boot-todos?{urlencode(todo_qs_parts)}")
    futures_spec["temporal"] = (cx, "GET", "/boot-temporal")

    rj_agent = {"cursor": "cursor-claude", "web": "web-claude"}.get(agent, agent)
    futures_spec["reflective_journal"] = (
        cx,
        "GET",
        f"/boot-reflective?{urlencode({'agent': rj_agent, 'limit': 5})}",
    )

    futures_spec["recent_mentions"] = (
        cx,
        "GET",
        f"/boot-recent-mentions?{urlencode({'days': 7, 'limit': 10})}",
    )
    futures_spec["skills"] = (
        cx,
        "GET",
        f"/entities?{urlencode({'type': 'agent_skill', 'limit': 50})}",
    )
    futures_spec["recent_work"] = (cx, "GET", "/boot-recent-work")
    futures_spec["rag_pipeline"] = (rag, _STARGATE_URL)

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
        "plan_phases": plan_phases,
        "in_flight_todos": in_flight_todos,
        "temporal_active": temporal_active,
        "expired_unresolved": expired_unresolved,
        "review_total": review_total,
        "rag_pipeline": rag_pipeline,
    }

"""Cortex named MCP tools — provenance, resolution, staging extras, and boot.

These are individually registered tools (not part of the unified
cortex(tool=..., arguments=...) surface). Lower-frequency operations accessed via
dispatch(tool="cortex_boot", ...) etc.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlencode

from mcp_events import record

from ._boot_helpers import (
    filter_stale_open_items,
    render_operational_context,
    safe_list,
)
from ._cortex_relay import _cx
from ._file_helpers import read_files_batch
from ._local_relay import relay as _relay

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)


_FULL_CAPACITY: dict[str, Any] = {
    "include_deadlines": True,
    "include_review_queue": True,
    "session_agent_filter": None,
    "session_limit": 3,
    "self_reflections_limit": 5,
}

_BOOT_PROFILES: dict[str, dict[str, Any]] = {
    "cursor": {**_FULL_CAPACITY, "self_entity_id": "ai_agent:cursor-claude"},
    "web": {**_FULL_CAPACITY, "self_entity_id": "ai_agent:web-claude"},
    "api": {**_FULL_CAPACITY, "self_entity_id": "ai_agent:api-claude"},
    "api_claude": {**_FULL_CAPACITY, "self_entity_id": "ai_agent:api-claude"},
    "oppie": {**_FULL_CAPACITY, "self_entity_id": "ai_agent:oppie"},
    "orion": {**_FULL_CAPACITY, "self_entity_id": "ai_agent:orion"},
    "bard": {**_FULL_CAPACITY, "self_entity_id": "ai_agent:bard"},
    "subagent": {**_FULL_CAPACITY},
}


def _resolve_transcript(
    transcript_id: str,
) -> dict[str, Any] | None:
    """Verify transcript entity exists, load markdown, traverse continues chain.

    Returns a continuation dict on success, or a dict with 'error' key on failure.
    None if transcript_id is empty.
    """
    if not transcript_id:
        return None

    clean_id = transcript_id.removeprefix("transcript:")
    entity_key = f"transcript:{clean_id}"

    entity_raw = _cx("GET", f"/entities/{quote(entity_key, safe=':')}")
    if "error" in entity_raw:
        return {
            "error": "transcript_not_found",
            "transcript_id": clean_id,
            "transcript_entity_id": entity_key,
            "detail": f"Entity {entity_key} not found in Cortex. Typo or stale reference?",
        }

    source_uri = entity_raw.get("source_uri") or ""
    transcript_md = ""
    if source_uri:
        md_results = read_files_batch([source_uri])
        md_content = md_results.get(source_uri)
        if isinstance(md_content, str):
            transcript_md = md_content

    chain_qs = urlencode({"node": entity_key, "edge_type": "continues", "hops": 5})
    chain_raw = _cx("GET", f"/edges/traverse?{chain_qs}")
    chain_edges: list[dict[str, Any]] = []
    if isinstance(chain_raw, dict):
        chain_edges = chain_raw.get("items", [])

    return {
        "transcript_id": clean_id,
        "entity_id": entity_key,
        "name": entity_raw.get("name", clean_id),
        "description": entity_raw.get("description", ""),
        "source_uri": source_uri,
        "markdown": transcript_md,
        "assertions": entity_raw.get("assertions", []),
        "chain": chain_edges,
    }


def run_cortex_boot(
    agent: str = "web",
    transcript_id: str = "",
) -> dict[str, Any]:
    """Build a persona-scoped Cortex boot briefing for internal callers and MCP.

    Returns a slim briefing card (~5-10KB) with a section manifest pointing to
    existing MCP tools for deeper pulls. Heavy data (full sessions, assertions,
    gated entities, legal contacts, file contents) is NOT inlined — agents pull
    on demand via the manifest hints.
    """
    from concurrent.futures import ThreadPoolExecutor

    transcript_continuation = _resolve_transcript(transcript_id)
    if transcript_continuation and "error" in transcript_continuation:
        return transcript_continuation

    t_boot = datetime.now(UTC)
    session_id = f"{agent}-{t_boot.strftime('%Y-%m-%d-%H%M')}"

    profile = _BOOT_PROFILES.get(agent, _BOOT_PROFILES["web"])

    unread_turns_qs = urlencode(
        {"to": agent, "unread": "true", "last": 10, "compact": "true"}
    )

    session_qs_parts: dict[str, str | int] = {"limit": profile.get("session_limit", 3)}
    if profile.get("session_agent_filter"):
        session_qs_parts["agent"] = profile["session_agent_filter"]
    session_qs = urlencode(session_qs_parts)

    # ── Parallel data fetch (slim set: only what the briefing card needs) ──
    futures_spec: dict[str, tuple[Any, ...]] = {
        "sessions": (_cx, "GET", f"/session-journals?{session_qs}"),
        "threads": (_relay, "agent-bus", "GET", "/threads?status=active"),
        "unread_turns": (
            _relay,
            "agent-bus",
            "GET",
            f"/turns?{unread_turns_qs}",
        ),
    }
    if profile.get("include_deadlines", True):
        futures_spec["deadlines"] = (_cx, "GET", "/deadlines")
    if profile.get("include_review_queue", True):
        futures_spec["staging"] = (
            _cx,
            "GET",
            "/staging?status=pending&limit=5",
        )

    todo_qs_parts: dict[str, Any] = {"limit": 15}
    if agent == "web":
        todo_qs_parts["domain_exclude"] = "infra,rag,pipeline,mcp,model_id"
    futures_spec["todos"] = (
        _cx,
        "GET",
        f"/boot-todos?{urlencode(todo_qs_parts)}",
    )
    futures_spec["temporal"] = (_cx, "GET", "/boot-temporal")

    rj_agent = {"cursor": "cursor-claude", "web": "web-claude"}.get(agent, agent)
    rj_qs = urlencode({"agent": rj_agent, "limit": 5})
    futures_spec["reflective_journal"] = (
        _cx,
        "GET",
        f"/boot-reflective?{rj_qs}",
    )

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
        futures_spec["self_reflections"] = (
            _cx,
            "GET",
            f"/assertions?{refl_qs}",
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        submitted = {k: pool.submit(*spec) for k, spec in futures_spec.items()}
        raw = {k: f.result() for k, f in submitted.items()}

    # ── Extract results ──
    sessions: list[dict[str, Any]] = safe_list(raw["sessions"])
    deadlines: list[dict[str, Any]] = safe_list(raw.get("deadlines", []))
    threads: list[dict[str, Any]] = safe_list(raw["threads"], "threads")
    unread_turns: list[dict[str, Any]] = safe_list(raw["unread_turns"], "turns")
    staging_items: list[dict[str, Any]] = safe_list(raw.get("staging", []))
    todos: list[dict[str, Any]] = safe_list(raw.get("todos", []))
    self_reflections: list[dict[str, Any]] = safe_list(raw.get("self_reflections", []))
    rj_entries: list[dict[str, Any]] = safe_list(raw.get("reflective_journal", []))
    rj_total: int = 0
    rj_raw = raw.get("reflective_journal", {})
    if isinstance(rj_raw, dict):
        rj_total = rj_raw.get("total", 0)

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

    # ── Write operational context to file (not inlined in response) ──
    op_ctx_path = f"notes/system/shared/operational-context-{agent}.md"
    ops_context = render_operational_context(
        agent=agent,
        unread_count=len(unread_turns),
        review_total=review_total,
    )
    try:
        _op_dir = Path("/data/files/notes/system/shared")
        _op_dir.mkdir(parents=True, exist_ok=True)
        (_op_dir / f"operational-context-{agent}.md").write_text(ops_context)
    except OSError:
        logger.warning("Could not write operational context to %s", op_ctx_path)

    # ── Build transcript continuation summary ──
    tc_summary: dict[str, Any] | None = None
    if transcript_continuation:
        tc = transcript_continuation
        summary = tc.get("description", "")
        if not summary and tc.get("assertions"):
            active = [a for a in tc["assertions"] if not a.get("superseded_by")]
            if active:
                summary = active[0].get("claim", "")
        tc_summary = {
            "entity_id": tc["entity_id"],
            "summary": summary,
        }

    # ── Unread thread summaries for the briefing card ──
    unread_threads = [
        {
            "id": t.get("id", ""),
            "slug": t.get("slug", ""),
            "unread": t.get("unread_count", 0),
        }
        for t in threads
        if t.get("unread_count", 0) > 0
    ]

    # ── Review queue top items for the briefing card ──
    review_top: list[dict[str, Any]] = []
    if staging_items:
        for s in staging_items[:3]:
            review_top.append(
                {
                    "id": s.get("id", "?"),
                    "name": s.get("name", s.get("entity_id", "?")),
                    "reason": s.get("reason", s.get("review_status", "pending")),
                }
            )

    # ── Render briefing card + manifest ──
    from ._boot_helpers import render_briefing_card

    card, manifest = render_briefing_card(
        deadlines=deadlines if profile.get("include_deadlines", True) else None,
        unread_count=len(unread_turns),
        unread_threads=unread_threads,
        review_total=review_total,
        review_top=review_top,
        last_session=sessions[0] if sessions else None,
        self_reflections=self_reflections or None,
        todos=todos or None,
        todo_total=len(todos),
        temporal_active=temporal_active or None,
        expired_unresolved=expired_unresolved or None,
        transcript_continuation=tc_summary,
        op_ctx_path=op_ctx_path,
        reflective_entries=rj_entries or None,
        reflective_total=rj_total,
    )

    logger.info(
        "cortex_boot: agent=%s card_size=%d manifest_sections=%d",
        agent,
        len(card),
        len(manifest),
    )
    record("mcp.cortex.boot", agent=agent)

    result: dict[str, Any] = {
        "session_id": session_id,
        "utc_now": t_boot.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "briefing_card": card,
        "sections_available": manifest,
        "operational_context_ref": op_ctx_path,
    }

    if tc_summary:
        result["continuation_transcript"] = {
            **tc_summary,
            "fetch_hint": (
                f"cortex(tool='entity_get', "
                f'arguments=\'{{"entity_id": "{tc_summary["entity_id"]}"}}\')'
            ),
        }

    return result


def register_cortex_named_tools(mcp: FastMCP) -> None:
    """Register named Cortex MCP tools: chunk, surface form, staging, and boot."""

    # --------------------------------------------------------------- chunks

    @mcp.tool(title="Cortex: Create Chunk")
    def cortex_chunk_create(
        content: str,
        source_uri: str | None = None,
        source_date: str | None = None,
        chunk_index: int | None = None,
        observer: str = "web",
        source_hash: str | None = None,
        model_version: str | None = None,
    ) -> dict[str, Any]:
        """Create a source chunk for provenance tracking.

        Args:
            content: The source text content.
            source_uri: Path to source (e.g. 'journals/2026/01/15.md').
            source_date: Date of the source material (YYYY-MM-DD).
            chunk_index: Position within the source document.
            observer: Who created this chunk (default 'web').
            source_hash: Content hash for deduplication.
            model_version: Model used for extraction.
        """
        body: dict[str, Any] = {
            "content": content,
            "observer": observer,
            **{
                key: val
                for key, val in [
                    ("source_uri", source_uri),
                    ("source_date", source_date),
                    ("chunk_index", chunk_index),
                    ("source_hash", source_hash),
                    ("model_version", model_version),
                ]
                if val is not None
            },
        }

        result = _cx("POST", "/chunks", body)
        if "error" not in result:
            logger.info("cortex_chunk_create: %s idx=%s", source_uri, chunk_index)
            record(
                "mcp.cortex.chunk_create",
                source_uri=source_uri,
                chunk_index=chunk_index,
            )
        else:
            logger.error("cortex_chunk_create failed: %s", result.get("error"))
        return result

    @mcp.tool(title="Cortex: Get Chunk")
    def cortex_chunk_get(chunk_id: int) -> dict[str, Any]:
        """Get a chunk by ID with its full content."""
        return _cx("GET", f"/chunks/{chunk_id}")

    # --------------------------------------------------------- surface forms

    @mcp.tool(title="Cortex: Create Surface Form")
    def cortex_surface_form_create(
        mention: str,
        entity_id: str,
        chunk_id: int,
        span_start: int | None = None,
        span_end: int | None = None,
        resolution_confidence: float | None = None,
        resolution_reasoning: str | None = None,
        context_hash: str | None = None,
        entity_type_hint: str | None = None,
    ) -> dict[str, Any]:
        """Create a surface form — a resolved entity mention. Populates the
        resolution cache so identical mentions resolve without an LLM call.

        Args:
            mention: The text as it appears in the source.
            entity_id: Resolved entity in type:slug format.
            chunk_id: Source chunk this mention appears in.
            context_hash: SHA-256 of lowercase(mention) + surrounding context.
        """
        body: dict[str, Any] = {
            "entity_id": entity_id,
            "form": mention,
            "chunk_id": chunk_id,
            "mention": mention,
            **{
                key: val
                for key, val in [
                    ("span_start", span_start),
                    ("span_end", span_end),
                    ("resolution_confidence", resolution_confidence),
                    ("resolution_reasoning", resolution_reasoning),
                    ("context_hash", context_hash),
                    ("entity_type_hint", entity_type_hint),
                ]
                if val is not None
            },
        }

        result = _cx("POST", "/surface-forms", body)
        if "error" not in result:
            logger.info("cortex_surface_form_create: %s -> %s", mention, entity_id)
            record(
                "mcp.cortex.surface_form_create", mention=mention, entity_id=entity_id
            )
        else:
            logger.error("cortex_surface_form_create failed: %s", result.get("error"))
        return result

    @mcp.tool(title="Cortex: Lookup Surface Form")
    def cortex_surface_form_lookup(
        mention: str,
        context_hash: str,
    ) -> dict[str, Any]:
        """Cache lookup: mention + context_hash -> entity_id.

        Returns {hit, entity_id, resolution_confidence, resolution_reasoning}.
        """
        return _cx(
            "GET",
            f"/surface-forms/cache?mention={quote(mention)}&context_hash={quote(context_hash)}",
        )

    # --------------------------------------------------------------- staging extras

    @mcp.tool(title="Cortex: List Staging")
    def cortex_staging_list(
        status: str | None = None,
        source_uri: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """List staging proposals with optional filters.

        Args:
            status: Filter — pending, approved, rejected, merged.
            source_uri: Filter by source URI.
            limit: Maximum results (1-500, default 50).

        Returns:
            StagingList, or {"error": "<message>"}.
        """
        params = {"limit": limit}
        if status is not None:
            params["status"] = status
        if source_uri is not None:
            params["source_uri"] = source_uri
        return _cx("GET", f"/staging?{urlencode(params)}")

    @mcp.tool(title="Cortex: Reject Staging")
    def cortex_staging_reject(staging_id: int, reviewer: str = "web") -> dict[str, Any]:
        """Reject a staging proposal.

        Args:
            staging_id: The staging proposal ID.
            reviewer: Who rejected (default 'web').

        Returns:
            Updated StagingItem, or {"error": "<message>"}.
        """
        result = _cx("POST", f"/staging/{staging_id}/reject", {"reviewer": reviewer})
        if "error" not in result:
            logger.error(
                "cortex_staging_reject failed for ID %d: %s",
                staging_id,
                result.get("error"),
            )
        else:
            logger.info("cortex_staging_reject: %d", staging_id)
        return result

    # --------------------------------------------------------------- boot

    @mcp.tool(title="Cortex Boot")
    def cortex_boot(
        agent: str = "web",
        transcript_id: str = "",
    ) -> dict[str, Any]:
        """Slim boot briefing for session start. Returns a compact briefing card
        (~5-10KB) with priority signals and a section manifest for on-demand pulls.

        The briefing card contains: deadlines, unread bus summary, review queue
        count, last session summary, top todos, self-observations, and temporal
        alerts. Heavy data (full sessions, assertions, gated entities, file
        contents) is NOT inlined — pull on demand via manifest hints.

        Args:
          agent         — agent profile: web, cursor, api, api_claude, oppie, orion, subagent (default: "web")
          transcript_id — if provided, loads continuation context for that transcript

        Key response fields:
          session_id             — server-minted ID; hold for entire session
          briefing_card          — compact Markdown briefing (~3-5KB)
          sections_available     — manifest of deeper-pull sections with fetch hints
          operational_context_ref — path to operational context file (read on demand)
        """
        return run_cortex_boot(
            agent=agent,
            transcript_id=transcript_id,
        )

    # --------------------------------------------------------- session close

    @mcp.tool(title="Session Close (Reminder)")
    def session_close(
        agent: str = "web",
        session_id: str = "",
    ) -> dict[str, Any]:
        """DEPRECATED — use cortex(tool="session_close", ...) for atomic closes.

        This tool only returns step-by-step instructions without performing
        the close.  The atomic version (cortex dispatch) validates transcript
        content, writes the file, and creates entity + journal row + edge
        in one call.

        Kept for backward compatibility.  Will be removed in a future release.

        Args:
          agent      — agent identity: web, cursor, api (default: "web")
          session_id — session ID from boot (if empty, mints one from current UTC)
        """
        from ._session_close import build_session_close

        result = build_session_close(agent=agent, session_id=session_id)
        if "error" not in result:
            result["_deprecation"] = (
                "This tool is deprecated. Use cortex(tool='session_close', "
                'arguments=\'{"session_id": "...", "agent": "...", '
                '"transcript_md": "...", "summary": "..."}\') instead. '
                "The atomic version prevents stub-only closes."
            )
            record(
                "mcp.session.close",
                agent=agent,
                transcript_id=result.get("transcript_id"),
            )
        return result

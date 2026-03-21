"""Cortex v2 dispatch-only tools — provenance, resolution, staging extras, and boot.

These remain as individually-named dispatch tools (not part of the unified
cortex(tool=..., arguments=...) surface). They are lower-frequency operations accessed via
dispatch(tool="cortex_boot", ...) etc.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlencode

from mcp_events import record

from ._boot_helpers import render_boot_narrative, safe_list
from ._file_helpers import read_files_batch
from .cortex import _cx
from .local_api import _relay

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)


def register_cortex_v2_tools(mcp: FastMCP) -> None:
    """Register dispatch-only Cortex v2 tools on the MCP server instance, including chunk, surface form, staging, and boot operations."""

    # --------------------------------------------------------------- chunks

    @mcp.tool()
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

    @mcp.tool()
    def cortex_chunk_get(chunk_id: int) -> dict[str, Any]:
        """Get a chunk by ID with its full content."""
        return _cx("GET", f"/chunks/{chunk_id}")

    # --------------------------------------------------------- surface forms

    @mcp.tool()
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

    @mcp.tool()
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

    @mcp.tool()
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

    @mcp.tool()
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

    @mcp.tool()
    def cortex_boot(
        agent: str = "web",
        pre_files: str = "",
        post_files: str = "",
    ) -> dict[str, Any]:
        """Unified boot briefing for session start.

        Consolidates boot into one call: deadlines, recent sessions, open
        investigations, agent-bus state, review queue, and optional reference
        files from the sandboxed files directory.

        Always use this at session start instead of calling the individual boot
        queries separately. Returns both structured JSON and a pre-rendered
        narrative that can be shown to the user directly.

        Args:
            agent: Which agent is booting — determines inbox filter (default 'web').
            pre_files: Comma-separated file paths loaded before the API briefing,
                e.g. "prompts/boot.md,prompts/ops.md". Typically prompts or
                operating instructions that should be read first.
            post_files: Comma-separated file paths loaded after the API briefing,
                e.g. "notes/legal/context.md". Typically reference material
                that rounds out session context.

        Returns:
            Boot briefing with deadlines, recent_sessions, open_investigations,
            agent_bus, review_queue, boot_narrative, pre_files, and post_files.
        """
        from concurrent.futures import ThreadPoolExecutor

        pre_list = (
            [p.strip() for p in pre_files.split(",") if p.strip()] if pre_files else []
        )
        post_list = (
            [p.strip() for p in post_files.split(",") if p.strip()]
            if post_files
            else []
        )
        pre_file_results = read_files_batch(pre_list) if pre_list else {}
        inbox_qs = urlencode(
            {"to": agent, "unread": "true", "last": 10, "compact": "true"}
        )
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {
                "deadlines": pool.submit(_cx, "GET", "/deadlines"),
                "sessions": pool.submit(_cx, "GET", "/session-journals?limit=3"),
                "assertions": pool.submit(
                    _cx, "GET", "/assertions?superseded=false&limit=50"
                ),
                "threads": pool.submit(
                    _relay, "agent-bus", "GET", "/threads?status=active"
                ),
                "inbox": pool.submit(_relay, "agent-bus", "GET", f"/turns?{inbox_qs}"),
                "staging": pool.submit(_cx, "GET", "/staging?status=pending&limit=30"),
            }
            raw = {k: f.result() for k, f in futures.items()}
        post_file_results = read_files_batch(post_list) if post_list else {}

        deadlines: list[dict[str, Any]] = safe_list(raw["deadlines"])
        sessions: list[dict[str, Any]] = safe_list(raw["sessions"])
        all_assertions: list[dict[str, Any]] = safe_list(raw["assertions"])
        threads: list[dict[str, Any]] = safe_list(raw["threads"], "threads")
        unread_turns: list[dict[str, Any]] = safe_list(raw["inbox"], "turns")
        staging_items: list[dict[str, Any]] = safe_list(raw["staging"])

        suspected = []
        hypothesized = []
        low_conf_unreviewed = []
        for a in all_assertions:
            confidence = a.get("confidence")
            if confidence == "suspected":
                suspected.append(a)
            elif confidence == "hypothesized":
                hypothesized.append(a)
            if confidence in ("suspected", "hypothesized") and not a.get(
                "human_reviewed"
            ):
                low_conf_unreviewed.append(a)
        review_total = len(staging_items) + len(low_conf_unreviewed)

        narrative = render_boot_narrative(
            deadlines=deadlines,
            sessions=sessions,
            suspected=suspected,
            hypothesized=hypothesized,
            threads=threads,
            unread=unread_turns,
            review_total=review_total,
        )

        logger.info(
            "cortex_boot: agent=%s deadlines=%d sessions=%d",
            agent,
            len(deadlines),
            len(sessions),
        )
        record("mcp.cortex.boot", agent=agent)

        return {
            "deadlines": deadlines,
            "recent_sessions": sessions,
            "open_investigations": {
                "suspected": suspected,
                "hypothesized": hypothesized,
            },
            "agent_bus": {
                "active_threads": [
                    {
                        "thread": t.get("id", ""),
                        "slug": t.get("slug", ""),
                        "turns": t.get("turns_count", 0),
                        "unread": t.get("unread_count", 0),
                    }
                    for t in threads
                ],
                "unread_turns": unread_turns,
            },
            "review_queue": {
                "staging_count": len(staging_items),
                "assertion_count": len(low_conf_unreviewed),
                "total": review_total,
            },
            "pre_files": pre_file_results,
            "post_files": post_file_results,
            "boot_narrative": narrative,
        }

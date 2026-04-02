"""Cortex named MCP tools — provenance, resolution, staging extras, and boot.

These are individually registered tools (not part of the unified
cortex(tool=..., arguments=...) surface). Lower-frequency operations accessed via
dispatch(tool="cortex_boot", ...) etc.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlencode

from mcp_events import record

from ._boot_helpers import (
    AGENT_PERSONA_SEEDS,
    build_gated_entities,
    render_boot_narrative,
    render_operational_context,
    safe_list,
)
from ._cortex_relay import _cx
from ._file_helpers import read_files_batch
from .local_api import _relay

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)


def _extract_entity_ids(sessions: list[dict[str, Any]]) -> list[str]:
    """Read entity_ids from the most recent session journal."""
    if not sessions:
        return []
    latest = sessions[0]
    ids = latest.get("entity_ids")
    if isinstance(ids, list):
        return [str(eid) for eid in ids if eid]
    return []


_BOOT_PROFILES: dict[str, dict[str, Any]] = {
    "cursor": {
        "include_deadlines": False,
        "include_review_queue": False,
        "include_investigations": False,
        "include_session_edges": True,
        "session_agent_filter": "cursor",
        "entity_type_exclude": "legal_matter,person,property,organization",
        "session_limit": 3,
        "assertion_limit": 50,
        "continuation_decision_limit": 5,
        "continuation_service_limit": 3,
        "boot_section_max_full": 3,
        "boot_section_max_oneline": 10,
        "boot_section_type_exclude": "legal_matter,person,property,organization",
        "session_edges_hours": 48,
        "session_edges_limit": 15,
    },
    "api": {
        "include_deadlines": False,
        "include_review_queue": False,
        "include_investigations": True,
        "include_session_edges": True,
        "session_agent_filter": "api",
        "entity_type_exclude": "legal_matter,person,property",
        "session_limit": 3,
        "assertion_limit": 50,
        "continuation_decision_limit": 5,
        "continuation_service_limit": 3,
        "boot_section_max_full": 5,
        "boot_section_max_oneline": 15,
        "boot_section_type_exclude": "legal_matter,person,property",
        "session_edges_hours": 48,
        "session_edges_limit": 15,
    },
    "web": {
        "include_deadlines": True,
        "include_review_queue": True,
        "include_investigations": True,
        "include_session_edges": True,
        "session_agent_filter": None,
        "entity_type_exclude": None,
        "session_limit": 3,
        "assertion_limit": 50,
        "continuation_decision_limit": 0,
        "continuation_service_limit": 0,
        "boot_section_max_full": 5,
        "boot_section_max_oneline": 15,
        "boot_section_type_exclude": None,
        "session_edges_hours": 48,
        "session_edges_limit": 20,
    },
    "oppie": {
        "include_deadlines": True,
        "include_review_queue": True,
        "include_investigations": True,
        "include_session_edges": True,
        "session_agent_filter": None,
        "entity_type_exclude": None,
        "session_limit": 3,
        "assertion_limit": 50,
        "continuation_decision_limit": 5,
        "continuation_service_limit": 3,
        "boot_section_max_full": 5,
        "boot_section_max_oneline": 15,
        "boot_section_type_exclude": None,
        "session_edges_hours": 48,
        "session_edges_limit": 20,
    },
    "subagent": {
        "include_deadlines": True,
        "include_review_queue": True,
        "include_investigations": True,
        "include_session_edges": True,
        "session_agent_filter": None,
        "entity_type_exclude": None,
        "session_limit": 3,
        "assertion_limit": 50,
        "continuation_decision_limit": 5,
        "continuation_service_limit": 3,
        "boot_section_max_full": 5,
        "boot_section_max_oneline": 15,
        "boot_section_type_exclude": None,
        "session_edges_hours": 48,
        "session_edges_limit": 20,
    },
}


def run_cortex_boot(
    agent: str = "web",
    pre_files: str = "",
    post_files: str = "",
) -> dict[str, Any]:
    """Build a persona-scoped Cortex boot briefing for internal callers and MCP."""
    from concurrent.futures import ThreadPoolExecutor

    t_boot = datetime.now(UTC)
    session_id = f"{agent}-{t_boot.strftime('%Y-%m-%d-%H%M')}"

    profile = _BOOT_PROFILES.get(agent, _BOOT_PROFILES["web"])

    if pre_files:
        pre_list = [p.strip() for p in pre_files.split(",") if p.strip()]
    else:
        default_seed = AGENT_PERSONA_SEEDS.get(agent, "")
        pre_list = [default_seed] if default_seed else []
    post_list = (
        [p.strip() for p in post_files.split(",") if p.strip()] if post_files else []
    )
    pre_file_results = read_files_batch(pre_list) if pre_list else {}
    unread_turns_qs = urlencode(
        {"to": agent, "unread": "true", "last": 10, "compact": "true"}
    )

    session_qs_parts: dict[str, str | int] = {"limit": profile.get("session_limit", 3)}
    if profile.get("session_agent_filter"):
        session_qs_parts["agent"] = profile["session_agent_filter"]
    session_qs = urlencode(session_qs_parts)

    assertion_qs_parts: dict[str, str | int] = {
        "superseded": "false",
        "limit": profile.get("assertion_limit", 50),
    }
    if profile.get("entity_type_exclude"):
        assertion_qs_parts["entity_type_exclude"] = profile["entity_type_exclude"]
    assertion_qs = urlencode(assertion_qs_parts)

    futures_spec: dict[str, tuple[Any, ...]] = {
        "sessions": (_cx, "GET", f"/session-journals?{session_qs}"),
        "assertions": (_cx, "GET", f"/assertions?{assertion_qs}"),
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
            "/staging?status=pending&limit=30",
        )

    decision_limit = profile.get("continuation_decision_limit", 0)
    service_limit = profile.get("continuation_service_limit", 0)
    if decision_limit > 0:
        cont_decision_qs = urlencode(
            {
                "entity_type": "decision",
                "superseded": "false",
                "limit": decision_limit,
            }
        )
        futures_spec["cont_decisions"] = (
            _cx,
            "GET",
            f"/assertions?{cont_decision_qs}",
        )
    if service_limit > 0:
        cont_service_qs = urlencode(
            {
                "entity_type": "service",
                "superseded": "false",
                "confidence": "believed",
                "limit": service_limit,
            }
        )
        futures_spec["cont_services"] = (
            _cx,
            "GET",
            f"/assertions?{cont_service_qs}",
        )

    todo_limit = 15
    todo_qs_parts: dict[str, Any] = {"limit": todo_limit}
    if agent == "cursor":
        todo_qs_parts["context"] = "code"
    futures_spec["todos"] = (
        _cx,
        "GET",
        f"/boot-todos?{urlencode(todo_qs_parts)}",
    )

    boot_section_qs_parts: dict[str, Any] = {
        "persona": agent,
        "agent": agent,
        "max_full": profile.get("boot_section_max_full", 5),
        "max_oneline": profile.get("boot_section_max_oneline", 15),
    }
    bs_type_exclude = profile.get("boot_section_type_exclude")
    if bs_type_exclude:
        boot_section_qs_parts["type_exclude"] = bs_type_exclude
    futures_spec["boot_sections"] = (
        _cx,
        "GET",
        f"/boot-sections?{urlencode(boot_section_qs_parts)}",
    )
    futures_spec["temporal"] = (_cx, "GET", "/boot-temporal")

    if profile.get("include_session_edges", True):
        edge_hours = profile.get("session_edges_hours", 48)
        edge_limit = profile.get("session_edges_limit", 15)
        edge_base = f"since_hours={edge_hours}&limit={edge_limit}"
        futures_spec["edges_supersedes"] = (
            _cx,
            "GET",
            f"/edges?edge_type=supersedes&{edge_base}",
        )
        futures_spec["edges_reasoning"] = (
            _cx,
            "GET",
            f"/edges?edge_type_exclude=supersedes,superseded_by&{edge_base}",
        )

    futures_spec["commitments"] = (_cx, "GET", "/boot-commitments?limit=10")
    if not profile.get("entity_type_exclude") or "legal_matter" not in profile.get(
        "entity_type_exclude", ""
    ):
        futures_spec["legal_contacts"] = (_cx, "GET", "/boot-legal-contacts")

    with ThreadPoolExecutor(max_workers=8) as pool:
        submitted = {k: pool.submit(*spec) for k, spec in futures_spec.items()}
        raw = {k: f.result() for k, f in submitted.items()}

    sessions: list[dict[str, Any]] = safe_list(raw["sessions"])

    gated_entity_ids = _extract_entity_ids(sessions)
    gated_raw: dict[str, Any] = {}
    if gated_entity_ids:
        gated_qs = urlencode(
            {"entity_ids": ",".join(gated_entity_ids), "per_entity": 5}
        )
        gated_raw = _cx("GET", f"/boot-gated?{gated_qs}")

    post_file_results = read_files_batch(post_list) if post_list else {}

    deadlines: list[dict[str, Any]] = safe_list(raw.get("deadlines", []))
    all_assertions: list[dict[str, Any]] = safe_list(raw["assertions"])
    threads: list[dict[str, Any]] = safe_list(raw["threads"], "threads")
    unread_turns: list[dict[str, Any]] = safe_list(raw["unread_turns"], "turns")
    staging_items: list[dict[str, Any]] = safe_list(raw.get("staging", []))

    cont_decisions: list[dict[str, Any]] = safe_list(raw.get("cont_decisions", []))
    cont_services: list[dict[str, Any]] = safe_list(raw.get("cont_services", []))
    todos: list[dict[str, Any]] = safe_list(raw.get("todos", []))

    boot_sections_raw = raw.get("boot_sections")
    boot_sections: dict[str, Any] | None = None
    if isinstance(boot_sections_raw, dict) and "sections" in boot_sections_raw:
        boot_sections = boot_sections_raw["sections"]

    edges_supersedes: list[dict[str, Any]] = safe_list(raw.get("edges_supersedes", []))
    edges_reasoning: list[dict[str, Any]] = safe_list(raw.get("edges_reasoning", []))

    commitments_raw = raw.get("commitments", {})
    commitments: list[dict[str, Any]] = (
        commitments_raw.get("items", []) if isinstance(commitments_raw, dict) else []
    )
    legal_contacts_raw = raw.get("legal_contacts", {})
    legal_contacts: list[dict[str, Any]] = (
        legal_contacts_raw.get("contacts", [])
        if isinstance(legal_contacts_raw, dict)
        else []
    )

    temporal_raw = raw.get("temporal", {})
    temporal_active: list[dict[str, Any]] = safe_list(
        temporal_raw.get("active", []) if isinstance(temporal_raw, dict) else []
    )
    temporal_upcoming: list[dict[str, Any]] = safe_list(
        temporal_raw.get("upcoming", []) if isinstance(temporal_raw, dict) else []
    )

    suspected = []
    hypothesized = []
    low_conf_unreviewed = []
    if profile.get("include_investigations", True):
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

    review_total: int | None = None
    if profile.get("include_review_queue", True):
        review_total = len(staging_items) + len(low_conf_unreviewed)

    gated_entities = build_gated_entities(
        gated_raw.get("entities", []) if isinstance(gated_raw, dict) else [],
        temporal_active,
    )

    narrative = render_boot_narrative(
        boot_sections=boot_sections,
        deadlines=deadlines if profile.get("include_deadlines", True) else None,
        temporal_active=temporal_active or None,
        temporal_upcoming=temporal_upcoming or None,
        sessions=sessions,
        suspected=suspected if profile.get("include_investigations", True) else None,
        hypothesized=hypothesized
        if profile.get("include_investigations", True)
        else None,
        threads=threads,
        unread=unread_turns,
        review_total=review_total,
        continuation_decisions=cont_decisions or None,
        continuation_services=cont_services or None,
        todos=todos or None,
        gated_entities=gated_entities or None,
        edges_supersedes=edges_supersedes or None,
        edges_reasoning=edges_reasoning or None,
        commitments=commitments or None,
        legal_contacts=legal_contacts or None,
    )

    logger.info(
        "cortex_boot: agent=%s profile=%s sessions=%d assertions=%d",
        agent,
        "custom" if agent not in _BOOT_PROFILES else agent,
        len(sessions),
        len(all_assertions),
    )
    record("mcp.cortex.boot", agent=agent)

    ops_context = render_operational_context(
        agent=agent,
        unread_count=len(unread_turns),
        review_total=review_total,
    )

    result: dict[str, Any] = {
        "session_id": session_id,
        "recent_sessions": sessions,
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
        "pre_files": pre_file_results,
        "post_files": post_file_results,
        "boot_narrative": narrative,
        "operational_context": ops_context,
        "operational_context_version": "v2.0",
    }

    if profile.get("include_deadlines", True):
        result["deadlines"] = deadlines
    if profile.get("include_investigations", True):
        result["open_investigations"] = {
            "suspected": suspected,
            "hypothesized": hypothesized,
        }
    if profile.get("include_review_queue", True):
        result["review_queue"] = {
            "staging_count": len(staging_items),
            "assertion_count": len(low_conf_unreviewed),
            "total": review_total,
        }
    if temporal_active or temporal_upcoming:
        result["temporal"] = {
            "active": temporal_active,
            "upcoming": temporal_upcoming,
        }
    if cont_decisions or cont_services or todos:
        result["continuation_state"] = {
            "decisions": cont_decisions,
            "service_observations": cont_services,
            "open_todos": todos,
        }
    if gated_entities:
        result["gated_entities"] = gated_entities
    if edges_supersedes or edges_reasoning:
        result["session_edges"] = {
            "supersession_chains": edges_supersedes,
            "reasoning_edges": edges_reasoning,
        }

    return result


def register_cortex_named_tools(mcp: FastMCP) -> None:
    """Register named Cortex MCP tools: chunk, surface form, staging, and boot."""

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
        """Persona-scoped boot briefing for session start (web, cursor, api, grok).

        Full docs: fs(op="md_read", sandbox="project", path="universal-llm-gateway/docs/tool-reference.md", section="cortex_boot")
        """
        return run_cortex_boot(agent=agent, pre_files=pre_files, post_files=post_files)

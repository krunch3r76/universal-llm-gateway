"""Chunk and surface-form MCP tool registrations."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from mcp_events import record

from .._cortex_relay import cx

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)


def register_data_tools(mcp: FastMCP) -> None:
    """Register chunk and surface-form tools on *mcp*."""

    @mcp.tool(title="Cortex: Create Chunk")
    def cortex_chunk_create(
        content: str,
        source_uri: str | None = None,
        source_date: str | None = None,
        chunk_index: int | None = None,
        observer: str = "cursor",
        source_hash: str | None = None,
        model_version: str | None = None,
    ) -> dict[str, Any]:
        """Create a source chunk for provenance tracking.

        Args:
            content: The source text content.
            source_uri: Path to source (e.g. 'journals/2026/01/15.md').
            source_date: Date of the source material (YYYY-MM-DD).
            chunk_index: Position within the source document.
            observer: Who created this chunk (default 'cursor').
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

        result = cx("POST", "/chunks", body)
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
        return cx("GET", f"/chunks/{chunk_id}")

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

        result = cx("POST", "/surface-forms", body)
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
        return cx(
            "GET",
            f"/surface-forms/cache?mention={quote(mention)}&context_hash={quote(context_hash)}",
        )

"""RAG tools — pipeline-powered semantic search and grounded answers.

Routes queries through Stargate's RAG pipelines (rag-context, rag-answer,
rag-answer-deep) for multi-query rewriting, RRF merge, entity synthesis,
relevance gating, and optionally iterative retrieval.

Connectivity: MCP container → Stargate host on port 9999 via
Stargate master via STARGATE_URL env (default: http://io:9999).
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, cast

import httpx
from mcp_events import monotonic_now, record
from transport_utils import make_sync_client

from ._rag_http import (
    handle_rag_call_error,
    rag_get,
)
from ._rag_http import (
    rag_post as _rag_post_http,
)
from ._rag_inflight import (
    SearchInFlightError,
    UnknownSearchError,
    admit_search,
    attach_search,
    search_key,
)
from ._rag_mapped import (
    LIST_MAPPED_ACTIVATION_NOTE,
    list_mapped_entries,
)
from ._rag_mapped import (
    resolve as resolve_mapped_pack,
)
from ._rag_search_exec import (
    STARGATE_URL,
    run_rag_search,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Default answer model used by the rag-answer pipelines; override via env when
# the pipelines are reconfigured to use a different model.
_SCOPES_TIMEOUT = 15.0
# Direct RAG REST API calls (no model inference — retrieval + ranking only).
_RAG_API_TIMEOUT = 30.0
_CURSOR_PREVIEW_MAX_TOP_K = max(1, int(os.getenv("MCP_RAG_PREVIEW_MAX_TOP_K", "10")))
_CURSOR_PREVIEW_SNIPPET_CHARS = max(
    100, int(os.getenv("MCP_RAG_PREVIEW_SNIPPET_CHARS", "300"))
)
_CURSOR_DETAIL_MAX_CHUNKS = max(1, int(os.getenv("MCP_RAG_DETAIL_MAX_CHUNKS", "20")))


def _rag_call(path: str, *, timeout: float) -> dict[str, Any]:
    """GET from Stargate passthrough and return parsed JSON."""
    return rag_get(STARGATE_URL, path, timeout=timeout)


def rag_post(path: str, body: dict[str, Any], *, timeout: float) -> dict[str, Any]:
    """POST JSON to Stargate passthrough and return parsed object payload."""
    return _rag_post_http(STARGATE_URL, path, body, timeout=timeout)


def _attach_rag_search(search_id: str) -> dict[str, Any]:
    """Serve a ``search_id`` poll: the finished envelope, ``in_flight`` again, or
    an ``error`` envelope when the handle was never issued or has expired."""
    try:
        ticket = attach_search(search_id)
    except UnknownSearchError:
        return {
            "error": (
                f"Unknown or expired search_id {search_id!r}; "
                "re-issue the original search."
            )
        }
    try:
        return ticket.stamp(ticket.wait())
    except SearchInFlightError as pending:
        return pending.envelope()


def _normalize_scope_override(
    scope: str | list[str] | None,
) -> tuple[str | list[str] | None, str | None]:
    """Normalize scope input into pipeline scope_override shape.

    Accepts either:
    - a single scope string
    - a comma-separated scope string
    - a list of scope strings

    Returns:
        (normalized_scope, error_message)
    """
    if scope is None:
        return None, None

    if isinstance(scope, list):
        normalized = [s.strip() for s in scope if isinstance(s, str) and s.strip()]
        if not normalized:
            return None, "Invalid scope list: no scopes provided."
        return normalized, None

    scope_val = scope.strip()
    if not scope_val:
        return None, "Invalid scope: empty string."

    normalized = [s.strip() for s in scope_val.split(",") if s.strip()]
    if not normalized:
        return None, "Invalid scope list: no scopes provided."
    if len(normalized) == 1:
        return normalized[0], None
    return normalized, None


def _normalize_prefix_override(
    prefix: str | list[str] | None,
) -> tuple[list[str] | None, str | None]:
    """Normalize optional source-prefix filters for RAG pipeline options."""
    if prefix is None:
        return None, None
    if isinstance(prefix, list):
        normalized = [p.strip() for p in prefix if isinstance(p, str) and p.strip()]
        if not normalized:
            return None, "Invalid prefix list: no prefixes provided."
        return normalized, None
    normalized = [p.strip() for p in prefix.split(",") if p.strip()]
    if not normalized:
        return None, "Invalid prefix list: no prefixes provided."
    return normalized, None


def register_rag_tools(mcp: FastMCP) -> None:
    """Register RAG pipeline tools on *mcp*."""

    @mcp.tool(title="RAG: List Mapped Packs")
    def rag_list_mapped() -> dict[str, Any]:
        """List keyed mapped-pack (scope, query) pairs without pack URIs.

        Returns activation recipes for ``rag(op=search, mapped=true)``. Agents
        discover keys here — do not fs-read pack bodies directly.

        Returns:
            On success:
                {
                  "status": "ok",
                  "entries": [
                    {
                      "scope": "...",
                      "query": "...",
                      "activate": {
                        "op": "search",
                        "mapped": true,
                        "scope": "...",
                        "query": "..."
                      },
                      "label": "..."  # only when present on index entry
                    },
                    ...
                  ],
                  "count": <int>,
                  "note": "<activation rule>"
                }
            Missing or unreadable index → ``entries: []`` with the same note.
        """
        t0 = monotonic_now()
        record("mcp.rag.mapped.list")
        entries = list_mapped_entries()
        duration = monotonic_now() - t0
        record(
            "mcp.rag.mapped.listed",
            count=len(entries),
            duration_s=round(duration, 3),
        )
        return {
            "status": "ok",
            "entries": entries,
            "count": len(entries),
            "note": LIST_MAPPED_ACTIVATION_NOTE,
        }

    @mcp.tool(title="RAG: List Scopes")
    def rag_list_scopes() -> dict[str, Any]:
        """List available retrieval scopes with coverage status.

        Merges scope definitions (prefixes, description) with live coverage
        data (indexed file counts) so agents see which scopes actually have
        content. Scopes with zero indexed files are flagged ``"status": "empty"``.

        Returns:
            On success:
                {
                  "scopes": ["scope_a", "scope_b", ...],
                  "details": {
                    "scope_a": {
                      "prefixes": [...],
                      "description": "...",
                      "indexed_files": 42,
                      "status": "indexed"
                    },
                    ...
                  }
                }
            On error: {"error": "<message>"}
        """
        t0 = monotonic_now()
        record("mcp.rag.scopes.called")
        try:
            payload = _rag_call("api/v1/rag/scopes", timeout=_SCOPES_TIMEOUT)
        except (
            httpx.ConnectError,
            httpx.TimeoutException,
            httpx.HTTPStatusError,
            httpx.RequestError,
            ValueError,
        ) as e:
            return handle_rag_call_error(e, endpoint_name="scopes")

        scopes_obj = payload.get("scopes", {})
        if not isinstance(scopes_obj, dict):
            record("mcp.rag.scopes.failed", error="invalid_payload")
            return {"error": "RAG scopes endpoint returned invalid payload."}

        coverage_by_scope: dict[str, int] = {}
        try:
            coverage_payload = _rag_call("api/v1/rag/coverage", timeout=_SCOPES_TIMEOUT)
            for name, scope_cov in coverage_payload.get("scopes", {}).items():
                if isinstance(scope_cov, dict):
                    coverage_by_scope[name] = int(scope_cov.get("total_indexed", 0))
        except (
            httpx.ConnectError,
            httpx.TimeoutException,
            httpx.HTTPStatusError,
            httpx.RequestError,
            ValueError,
        ):
            logger.warning("Coverage enrichment failed; proceeding without it")

        scopes_typed = cast(dict[str, object], scopes_obj)
        scope_names = sorted(scopes_typed.keys())
        details: dict[str, object] = {}
        for scope_name in scope_names:
            raw_detail = scopes_typed.get(scope_name)
            if isinstance(raw_detail, dict):
                detail = dict(raw_detail)
            else:
                detail = {}
            indexed_files = coverage_by_scope.get(scope_name, 0)
            detail["indexed_files"] = indexed_files
            detail["status"] = "indexed" if indexed_files > 0 else "empty"
            details[scope_name] = detail
        duration = monotonic_now() - t0
        record(
            "mcp.rag.scopes.completed",
            duration_s=round(duration, 3),
            count=len(scope_names),
        )
        return {"scopes": scope_names, "details": details}

    @mcp.tool(title="RAG: Coverage")
    def rag_coverage() -> dict[str, Any]:
        """Show per-scope, per-prefix indexed file counts and last-indexed timestamps.

        Use this to check what's actually indexed in each retrieval scope
        before running searches. Surfaces blind spots where a scope prefix
        has zero indexed files or stale data.

        Returns:
            On success:
                {
                  "scopes": {
                    "project": {
                      "prefixes": [
                        {"path": "/path/to/docs", "indexed_files": 18, "last_indexed": "2026-03-16T06:10:47"},
                        {"path": "/path/to/journal", "indexed_files": 4, "last_indexed": "2026-03-15T22:33:53"}
                      ],
                      "total_indexed": 22
                    }
                  }
                }
            On error: {"error": "<message>"}
        """
        t0 = monotonic_now()
        record("mcp.rag.coverage.called")
        try:
            payload = _rag_call("api/v1/rag/coverage", timeout=_SCOPES_TIMEOUT)
        except (
            httpx.ConnectError,
            httpx.TimeoutException,
            httpx.HTTPStatusError,
            httpx.RequestError,
            ValueError,
        ) as e:
            return handle_rag_call_error(e, endpoint_name="coverage")

        duration = monotonic_now() - t0
        scope_count = len(payload.get("scopes", {}))
        record(
            "mcp.rag.coverage.completed",
            duration_s=round(duration, 3),
            scope_count=scope_count,
        )
        return payload

    @mcp.tool(title="RAG: Search")
    def rag_search(
        query: str,
        top_k: int = 20,
        limit: int | None = None,
        scope: str | list[str] | None = None,
        prefix: str | list[str] | None = None,
        mapped: bool = False,
        search_id: str | None = None,
        step_overrides: dict[str, Any] | None = None,
        skip_steps: list[str] | None = None,
        hyde_enabled: bool | None = None,
        rerank_enabled: bool | None = None,
        catalog_retry_enabled: bool | None = None,
    ) -> dict[str, Any]:
        """PRIMARY (and only) agent surface for MCP RAG retrieval. Returns raw
        context chunks with source labels for the agent to cite, gate (lawyer-stance),
        and reason over. Use by default.

        Uses multi-query rewriting, reciprocal rank fusion, entity/relation
        merging, and property index boost. `limit` accepted as alias for
        `top_k` (normalizes in function; covers both rag(op=) and dispatch paths).

        IMPORTANT: query must be natural language. Boolean operators (OR, AND)
        degrade dense retrieval — use parallel calls per concept instead.

        Parallel calls are safe: identical requests share one backend search, and
        every envelope carries ``search_id``. A call that outlives the wait budget
        (``MCP_RAG_SEARCH_WAIT_S``, default 90 s) returns ``status: "in_flight"``
        with ``search_id`` — the search is still running, not failed. Poll with
        ``search_id=`` (or re-issue the identical call); neither starts a new search.

        Call rag_list_scopes() for the current set of valid scope names.
        Scope-first discipline: unscoped searches use the direct pipeline's
        default scope (`scope_source=default_scope`) unless a classifier path is
        active — do NOT read an empty/thin result as 'absent from corpus' without
        re-searching explicit scopes.

        When ``mapped=true``, exact (scope, query) lookup against
        ``config/mcp/rag_mapped_index.yaml`` serves a durable pack body through
        the identical search envelope; miss falls through to live rag-context.

        Full docs: fs(op="md_read", sandbox="workspaces", path="universal-llm-gateway/docs/tool-reference.md", section="rag_search")

        Args:
            query: Natural language search query.
            top_k: Maximum chunks after RRF merge (default 20).
            limit: Alias for top_k (preferred in some MCP contexts; mutually
                exclusive with explicit top_k if values differ).
            scope: Named scope filter as single string, comma-separated string,
                or list of scope strings (e.g. "research",
                "research, knowledge_systems",
                ["research_small_llm", "knowledge_systems"]).
            prefix: Source path prefix filter as a comma-separated string or
                list (e.g. "/docs/research", ["/docs/research", "/docs/engram"]).
                Mutually exclusive with scope.
            mapped: When True, try exact (scope, query) durable-pack lookup
                first; on miss, run live rag-context.
            search_id: Poll handle from an earlier ``in_flight`` envelope. When
                given, every other argument is ignored and the call attaches to
                that search (result, still ``in_flight``, or ``error`` if the
                handle is unknown/expired).

        Returns:
            On success: {"status": "ok", "pipeline": "rag-context",
                         "content_length": <int>, "duration_s": <float>,
                         "context": "<assembled context with source labels>",
                         "search_id": "rs-…", ["attached": true], ["cache_hit": true],
                         "retrieval": {resolved_scope, scope_confidence,
                                       chunks_found, scope_rejected,
                                       scope_source, auto_classified, ...}}
            Unscoped calls also include ``scope_note`` when scope_source is
            ``default_scope`` or ``classifier``.
            Still running: {"status": "in_flight", "search_id", "elapsed_s",
                            "wait_budget_s", "poll"} — not an error.
            On error:   {"error": "<message>", "search_id"?} (+ ``scope_note`` when unscoped)
        """
        if search_id:
            return _attach_rag_search(search_id)
        if mapped:
            hit = resolve_mapped_pack(query, scope)
            if hit is not None:
                return hit

        pipeline_options: dict[str, Any] = {}
        scope_override, scope_error = _normalize_scope_override(scope)
        prefixes, prefix_error = _normalize_prefix_override(prefix)
        if scope_error:
            return {"error": scope_error}
        if prefix_error:
            return {"error": prefix_error}
        if scope_override is not None and prefixes is not None:
            return {"error": "scope and prefix are mutually exclusive; set only one."}

        unscoped = scope_override is None and prefixes is None

        # Parameter ergonomics: limit alias for top_k (Finding 3). Covers
        # both rag(op=...) router and dispatch(tool="rag_search") paths.
        if limit is not None:
            if top_k != 20 and limit != top_k:
                return {"error": "conflicting top_k and limit values; pass only one"}
            top_k = limit
        if scope_override is not None:
            pipeline_options["scope_override"] = scope_override
        if prefixes is not None:
            pipeline_options["rag_source_prefixes"] = prefixes
        if top_k != 20:
            pipeline_options["rag_max_chunks"] = top_k
        pipeline_options["include_retrieval_metadata"] = True
        if step_overrides:
            pipeline_options["step_overrides"] = step_overrides
        if skip_steps:
            pipeline_options["skip_steps"] = skip_steps
        if hyde_enabled is not None:
            pipeline_options["hyde_enabled"] = hyde_enabled
        if rerank_enabled is not None:
            pipeline_options["rerank_enabled"] = rerank_enabled
        if catalog_retry_enabled is not None:
            pipeline_options["catalog_retry_enabled"] = catalog_retry_enabled
        from systems.pipeline.core.step_controls import finalize_relay_pipeline_options

        pipeline_options, step_controls_error = finalize_relay_pipeline_options(
            "rag-context",
            pipeline_options,
        )
        if step_controls_error:
            return {"error": step_controls_error}

        def _start() -> dict[str, Any]:
            return run_rag_search(
                query,
                scope=scope,
                prefixes=prefixes,
                pipeline_options=pipeline_options,
                unscoped=unscoped,
            )

        ticket = admit_search(search_key(query, pipeline_options), _start)
        try:
            return ticket.stamp(ticket.wait())
        except SearchInFlightError as pending:
            return pending.envelope()

    @mcp.tool(title="RAG: Search Preview")
    def rag_search_preview(
        query: str,
        top_k: int = 5,
        scope: str | list[str] | None = None,
        prefix: str | list[str] | None = None,
        snippet_chars: int = 300,
    ) -> dict[str, Any]:
        """Return bounded retrieval previews for Cursor-safe RAG exploration.

        Results include truncated snippets and chunk references for explicit
        follow-up detail fetches.
        """
        safe_k = max(1, min(top_k, _CURSOR_PREVIEW_MAX_TOP_K))
        safe_snippet = max(100, min(snippet_chars, _CURSOR_PREVIEW_SNIPPET_CHARS))
        scope_override, scope_error = _normalize_scope_override(scope)
        prefixes, prefix_error = _normalize_prefix_override(prefix)
        if scope_error:
            return {"error": scope_error}
        if prefix_error:
            return {"error": prefix_error}
        if scope_override is not None and prefixes is not None:
            return {"error": "scope and prefix are mutually exclusive; set only one."}

        body: dict[str, Any] = {"query": query, "top_k": safe_k}
        if scope_override is not None:
            body["scope"] = scope_override
        if prefixes is not None:
            body["source_prefixes"] = prefixes

        t0 = monotonic_now()
        record("mcp.rag.preview.called", top_k=safe_k)
        try:
            payload = rag_post("api/v1/rag/search", body, timeout=_RAG_API_TIMEOUT)
        except (
            httpx.ConnectError,
            httpx.TimeoutException,
            httpx.HTTPStatusError,
            httpx.RequestError,
            ValueError,
        ) as exc:
            return handle_rag_call_error(exc, endpoint_name="search_preview")

        chunks = payload.get("chunks", [])
        metadata = payload.get("metadata", [])
        items: list[dict[str, Any]] = []
        if isinstance(chunks, list):
            for idx, text in enumerate(chunks):
                if not isinstance(text, str):
                    continue
                md = (
                    metadata[idx]
                    if isinstance(metadata, list) and idx < len(metadata)
                    else {}
                )
                source = md.get("source") if isinstance(md, dict) else ""
                chunk_index = md.get("chunk_index") if isinstance(md, dict) else None
                items.append(
                    {
                        "source": source,
                        "chunk_index": chunk_index,
                        "snippet": text[:safe_snippet],
                    }
                )

        duration = monotonic_now() - t0
        record(
            "mcp.rag.preview.completed",
            count=len(items),
            duration_s=round(duration, 3),
        )
        return {"items": items, "count": len(items), "top_k": safe_k}

    @mcp.tool(title="RAG: Get Chunks")
    def rag_get_chunks(source: str, chunk_indices: list[int]) -> dict[str, Any]:
        """Fetch explicit chunk text by source and chunk indices."""
        if not chunk_indices:
            return {"error": "chunk_indices is required"}

        normalized_indices: list[int] = []
        for value in chunk_indices:
            try:
                normalized_indices.append(int(value))
            except (TypeError, ValueError):
                return {"error": "chunk_indices must contain only integers"}

        if len(normalized_indices) > _CURSOR_DETAIL_MAX_CHUNKS:
            return {
                "error": (
                    f"Maximum {_CURSOR_DETAIL_MAX_CHUNKS} chunk indices are "
                    "allowed per call."
                )
            }

        body = {"groups": [{"source": source, "chunk_indices": normalized_indices}]}
        try:
            payload = rag_post(
                "api/v1/rag/chunks_by_index",
                body,
                timeout=_RAG_API_TIMEOUT,
            )
        except (
            httpx.ConnectError,
            httpx.TimeoutException,
            httpx.HTTPStatusError,
            httpx.RequestError,
            ValueError,
        ) as exc:
            return handle_rag_call_error(exc, endpoint_name="chunks_by_index")

        chunks = payload.get("chunks", [])
        count = len(chunks) if isinstance(chunks, list) else 0
        record("mcp.rag.chunks.fetched", source=source, count=count)
        return payload

    @mcp.tool(title="RAG: Refresh Corpus Hints")
    def rag_refresh_corpus_hints(
        scope: str | None = None,
        entity_boost_hyphen: float = 1.3,
        entity_boost_single: float = 1.2,
        blocklist_override: list[str] | None = None,
        extra_blocklist: list[str] | None = None,
    ) -> dict[str, Any]:
        """Refresh corpus hints for one or all scopes with optional tuning.

        Corpus hints are discriminative vocabulary terms used by query
        rewriting to constrain LLM-generated queries to terms that actually
        exist in the corpus. After indexing new content into a scope, run
        this to generate/update its hints.

        The default tuning is optimized for research paper corpora. For
        project-doc or design-thread corpora, set entity_boost_hyphen=1.0,
        entity_boost_single=1.0, and blocklist_override=[] to disable
        shape boosts and the generic blocklist.

        Args:
            scope: Refresh hints for this scope only. None = all scopes.
            entity_boost_hyphen: Score multiplier for hyphenated terms
                (e.g. "chain-of-thought"). Default 1.3.
            entity_boost_single: Score multiplier for single-token terms
                (e.g. "NEPOMUK"). Default 1.2.
            blocklist_override: If set, replaces the default generic
                blocklist entirely. Pass [] to disable blocklisting.
            extra_blocklist: Additional terms to add to the active blocklist.

        Returns:
            On success: {"scopes_updated": [...], "terms_by_scope": {...}}
            On error: {"error": "<message>"}
        """
        t0 = monotonic_now()
        record("mcp.rag.hints.refresh.called", scope=scope)
        body: dict[str, Any] = {
            "entity_boost_hyphen": entity_boost_hyphen,
            "entity_boost_single": entity_boost_single,
        }
        if scope is not None:
            body["scope"] = scope
        if blocklist_override is not None:
            body["blocklist_override"] = blocklist_override
        if extra_blocklist is not None:
            body["extra_blocklist"] = extra_blocklist

        url = "/api/v1/rag/refresh_corpus_hints"
        try:
            with make_sync_client(STARGATE_URL, timeout=60.0) as client:
                resp = client.post(url, json=body)
                resp.raise_for_status()
                payload = resp.json()
        except (
            httpx.ConnectError,
            httpx.TimeoutException,
            httpx.HTTPStatusError,
            httpx.RequestError,
        ) as e:
            return handle_rag_call_error(e, endpoint_name="refresh_corpus_hints")

        duration = monotonic_now() - t0
        record(
            "mcp.rag.hints.refresh.completed",
            scope=scope,
            duration_s=round(duration, 3),
            scopes_updated=payload.get("scopes_updated", []),
        )
        return payload

    @mcp.tool(title="RAG: Recon")
    def rag_recon(
        label: str,
        themes: list[dict[str, Any]],
        top_k: int = 20,
        durable_sink: str | None = None,
    ) -> dict[str, Any]:
        """Run labeled per-theme RAG recon and persist durable sidecars.

        Executes scoped searches per theme, writes markdown sidecars via the
        configured DurableSink (cortex default, filesystem/null fallback), and
        returns backend selection metadata plus resolvable URIs for successful writes.
        """
        from ._rag_recon import execute_rag_recon

        return execute_rag_recon(
            label,
            themes,
            top_k=top_k,
            durable_sink=durable_sink,
        )

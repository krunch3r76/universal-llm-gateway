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
from provider_model_limits import local_model_inference_timeout, rag_pipeline_timeout
from transport_utils import make_sync_client

from ._rag_http import (
    handle_rag_call_error,
    rag_get,
)
from ._rag_http import (
    rag_post as _rag_post_http,
)
from ._rag_retrieval_metadata import (
    envelope_retrieval_fields,
    retrieval_metadata_from_response,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

STARGATE_URL = os.environ.get("STARGATE_URL", "http://io:9999")
# Default rerank/answer model names used by the rag-context and rag-answer pipelines.
# Override via env vars when pipelines are reconfigured to use different models.
_RERANK_MODEL_DEFAULT = os.environ.get("RAG_RERANK_MODEL", "qwen3_9b")
_ANSWER_MODEL_DEFAULT = os.environ.get("RAG_ANSWER_MODEL", "phi4")
# Extra seconds added to the httpx client timeout beyond the pipeline wall-clock.
_HTTP_BUFFER_S = 10.0
_SCOPES_TIMEOUT = 15.0
# Direct RAG REST API calls (no model inference — retrieval + ranking only).
_RAG_API_TIMEOUT = 30.0
_RAG_METADATA_DB = os.environ.get(
    "RAG_METADATA_DB_PATH", "/data/rag-store/rag_metadata.db"
)
_CURSOR_PREVIEW_MAX_TOP_K = max(1, int(os.getenv("MCP_RAG_PREVIEW_MAX_TOP_K", "10")))
_CURSOR_PREVIEW_SNIPPET_CHARS = max(
    100, int(os.getenv("MCP_RAG_PREVIEW_SNIPPET_CHARS", "300"))
)
_CURSOR_DETAIL_MAX_CHUNKS = max(1, int(os.getenv("MCP_RAG_DETAIL_MAX_CHUNKS", "20")))


def _pipeline_call(
    model: str,
    messages: list[dict[str, Any]],
    *,
    pipeline_options: dict[str, Any] | None = None,
    timeout: float,
) -> dict[str, Any]:
    """POST to Stargate /v1/chat/completions. Raises httpx errors on failure."""
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
    }
    if pipeline_options:
        body["pipeline_options"] = pipeline_options

    url = "/v1/chat/completions"
    with make_sync_client(STARGATE_URL, timeout=timeout) as client:
        resp = client.post(url, json=body)
        resp.raise_for_status()
        return resp.json()


def _rag_call(path: str, *, timeout: float) -> dict[str, Any]:
    """GET from Stargate passthrough and return parsed JSON."""
    return rag_get(STARGATE_URL, path, timeout=timeout)


def rag_post(path: str, body: dict[str, Any], *, timeout: float) -> dict[str, Any]:
    """POST JSON to Stargate passthrough and return parsed object payload."""
    return _rag_post_http(STARGATE_URL, path, body, timeout=timeout)


def _handle_pipeline_error(
    exc: BaseException,
    pipeline: str,
    t0: float,
    user_message: str,
) -> dict[str, str]:
    """Log, record mcp.rag.pipeline.failed, and return error dict for HTTPX pipeline failures."""
    extra: dict[str, Any] = {}
    surfaced_message = user_message
    if isinstance(exc, httpx.TimeoutException):
        duration = monotonic_now() - t0
        error_type = "timeout"
        log_message = f"Pipeline timed out after {duration:.1f}s: {exc}"
        extra["duration_s"] = round(duration, 3)
    elif isinstance(exc, httpx.ConnectError):
        error_type = str(exc)
        log_message = f"Stargate connection failed: {exc}"
    elif isinstance(exc, httpx.HTTPStatusError):
        error_type = f"{exc.response.status_code}"
        log_message = f"Pipeline HTTP error: {exc}"
        try:
            payload = exc.response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            nested = payload.get("detail", payload.get("error", {}))
            if isinstance(nested, dict) and nested.get("message"):
                surfaced_message = str(nested["message"])
            elif isinstance(nested, str) and nested.strip():
                surfaced_message = nested
    else:
        error_type = str(exc)
        log_message = f"Pipeline request error: {exc}"

    logger.warning(log_message, exc_info=True)
    record("mcp.rag.pipeline.failed", pipeline=pipeline, error=error_type, **extra)
    return {"error": surfaced_message}


def _extract_content(response: dict[str, Any]) -> str:
    """Extract message content from an OpenAI-format chat completions response.

    Args:
        response: Raw dict from the chat completions API.

    Returns:
        Content string from the first choice's message, or empty string if absent.
    """
    choices = response.get("choices", [])
    if not choices:
        return ""
    return choices[0].get("message", {}).get("content", "")


_SCOPE_NOTE_CLASSIFIER = (
    "Auto-scope-classified search (no scope= given). ~68 scopes exist. "
    "Before concluding absence-of-evidence, call rag(op='list_scopes') and "
    "re-search with an explicit scope= over relevant domains."
)
_SCOPE_NOTE_DEFAULT = (
    "Broad default-scope search (no scope= given). ~68 scopes exist. "
    "Before concluding absence-of-evidence, call rag(op='list_scopes') and "
    "re-search with an explicit scope= over relevant domains."
)
_ZERO_RESULT_UNSCOPED_CAVEAT = (
    "Auto-scoped ≠ corpus-wide. Before concluding absence-of-evidence, "
    "call rag(op='list_scopes') and re-search with an explicit scope=."
)


def _unscoped_scope_note(retrieval_fields: dict[str, Any]) -> str | None:
    """Return scope advisory text for unscoped calls based on ``scope_source``."""
    retrieval = retrieval_fields.get("retrieval", {})
    scope_source = retrieval.get("scope_source", "default_scope")
    if scope_source == "classifier":
        return _SCOPE_NOTE_CLASSIFIER
    if scope_source == "default_scope":
        return _SCOPE_NOTE_DEFAULT
    return None


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


def _scope_metadata_from_db() -> dict[str, dict[str, Any]]:
    """Return optional per-scope metadata from rag_metadata.db.

    Missing DB files, missing tables, and malformed/corrupt database states
    degrade to an empty mapping so `rag_list_scopes` still returns registry data.
    """
    import contextlib
    import sqlite3

    if not os.path.exists(_RAG_METADATA_DB):
        return {}

    try:
        with contextlib.closing(
            sqlite3.connect(f"file:{_RAG_METADATA_DB}?mode=ro", uri=True)
        ) as conn:
            article_rows = conn.execute(
                "SELECT scope, COUNT(*) AS article_count FROM articles GROUP BY scope"
            ).fetchall()
            topic_rows = conn.execute(
                "SELECT scope, term FROM corpus_hints "
                "WHERE prefix = 'prop.topic@@' "
                "ORDER BY scope ASC, score DESC, term ASC"
            ).fetchall()
    except sqlite3.Error:
        logger.exception(
            "Failed to enrich scopes from metadata DB %s", _RAG_METADATA_DB
        )
        return {}

    result: dict[str, dict[str, Any]] = {}
    for scope, article_count in article_rows:
        if isinstance(scope, str):
            result.setdefault(scope, {})["article_count"] = int(article_count)
    for scope, term in topic_rows:
        if not (isinstance(scope, str) and isinstance(term, str)):
            continue
        topics = cast(
            list[str], result.setdefault(scope, {}).setdefault("top_topics", [])
        )
        if len(topics) < 5 and term not in topics:
            topics.append(term)
    return result


def register_rag_tools(mcp: FastMCP) -> None:
    """Register RAG pipeline tools on *mcp*."""

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
        db_metadata = _scope_metadata_from_db()
        details: dict[str, object] = {}
        for scope_name in scope_names:
            raw_detail = scopes_typed.get(scope_name)
            if isinstance(raw_detail, dict):
                detail = dict(raw_detail)
            else:
                detail = {}
            detail.update(db_metadata.get(scope_name, {}))
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
    ) -> dict[str, Any]:
        """PRIMARY (and only) agent surface for MCP RAG retrieval. Returns raw
        context chunks with source labels for the agent to cite, gate (lawyer-stance),
        and reason over. Use by default. The rag_answer pipeline is buried in MCP
        and should not be used by agents (only for debugging the pipeline itself
        via direct dispatch or /v1/chat/completions with model=rag-answer*).

        Uses multi-query rewriting, reciprocal rank fusion, entity/relation
        merging, and property index boost. `limit` accepted as alias for
        `top_k` (normalizes in function; covers both rag(op=) and dispatch paths).

        IMPORTANT: query must be natural language. Boolean operators (OR, AND)
        degrade dense retrieval — use parallel calls per concept instead.

        Call rag_list_scopes() for the current set of valid scope names.
        Scope-first discipline: unscoped searches use the direct pipeline's
        default scope (`scope_source=default_scope`) unless a classifier path is
        active — do NOT read an empty/thin result as 'absent from corpus' without
        re-searching explicit scopes.

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

        Returns:
            On success: {"status": "ok", "pipeline": "rag-context",
                         "content_length": <int>, "duration_s": <float>,
                         "context": "<assembled context with source labels>",
                         "retrieval": {resolved_scope, scope_confidence,
                                       chunks_found, scope_rejected,
                                       scope_source, auto_classified, ...}}
            Unscoped calls also include ``scope_note`` when scope_source is
            ``default_scope`` or ``classifier``.
            On error:   {"error": "<message>"} (+ ``scope_note`` when unscoped)
        """
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

        record_args: dict[str, Any] = {
            "pipeline": "rag-context",
            "query": query,
            "scope": scope,
        }
        if prefixes is not None:
            record_args["prefix"] = prefixes
        t0 = monotonic_now()
        record("mcp.rag.pipeline.called", **record_args)

        rerank_model = pipeline_options.get("rerank_model", _RERANK_MODEL_DEFAULT)
        pipeline_timeout = rag_pipeline_timeout(rerank_model)
        pipeline_options["timeout_seconds"] = pipeline_timeout

        try:
            result = _pipeline_call(
                "rag-context",
                [{"role": "user", "content": query}],
                pipeline_options=pipeline_options,
                timeout=pipeline_timeout + _HTTP_BUFFER_S,
            )
        except httpx.TimeoutException as e:
            user_message = "Pipeline timed out. The query may be too complex."
            return _handle_pipeline_error(e, "rag-context", t0, user_message)
        except httpx.ConnectError as e:
            user_message = "Pipeline not available. Stargate may not be running."
            return _handle_pipeline_error(e, "rag-context", t0, user_message)
        except httpx.HTTPStatusError as e:
            user_message = (
                f"Pipeline error: {e.response.status_code} {e.response.reason_phrase}"
            )
            return _handle_pipeline_error(e, "rag-context", t0, user_message)
        except httpx.RequestError as e:
            user_message = f"Pipeline request failed: {e}"
            return _handle_pipeline_error(e, "rag-context", t0, user_message)

        content = _extract_content(result) if result else ""
        duration = monotonic_now() - t0
        retrieval_fields = envelope_retrieval_fields(
            retrieval_metadata_from_response(result),
        )
        scope_note = _unscoped_scope_note(retrieval_fields) if unscoped else None

        if not content:
            record(
                "mcp.rag.pipeline.completed",
                pipeline="rag-context",
                duration_s=round(duration, 3),
                empty=True,
                query=query,
                scope=scope,
                prefix=prefixes,
            )
            zero_note = _ZERO_RESULT_UNSCOPED_CAVEAT if unscoped else None
            error = "Pipeline returned empty results."
            if zero_note:
                error = f"{error} {zero_note}"
            return {
                "error": error,
                **({"zero_result_caveat": zero_note} if zero_note else {}),
                **({"scope_note": scope_note} if scope_note else {}),
                **retrieval_fields,
            }

        logger.info(
            "rag_search: query=%r scope=%s prefix=%s → %d chars in %.1fs",
            query,
            scope,
            prefixes,
            len(content),
            duration,
        )
        record(
            "mcp.rag.pipeline.completed",
            pipeline="rag-context",
            duration_s=round(duration, 3),
            content_length=len(content),
            scope=scope,
            prefix=prefixes,
        )
        return {
            "status": "ok",
            "pipeline": "rag-context",
            "content_length": len(content),
            "duration_s": round(duration, 3),
            "context": content,
            **({"scope_note": scope_note} if scope_note else {}),
            **retrieval_fields,
        }

    @mcp.tool(title="RAG: Answer (DEBUG ONLY)")
    def rag_answer(
        question: str,
        scope: str | list[str] | None = None,
        prefix: str | list[str] | None = None,
        deep: bool = False,
    ) -> dict[str, Any]:
        """DEBUG-ONLY: Calls the buried rag-answer / rag-answer-deep pipeline.

        Agents MUST NOT use this (or rag(op="answer")). The only legitimate
        use is debugging the RAG answer pipeline itself via MCP (e.g. to call
        the underlying /v1/chat/completions endpoint with model=rag-answer*).
        For all agent work, use rag_search / rag(op="search") exclusively.

        Has relevance gate and optional deep=True iterative retrieval.
        See rag_search docstring and tool-reference.md for agent policy.

        Full docs: fs(op="md_read", sandbox="workspaces", path="universal-llm-gateway/docs/tool-reference.md", section="rag_answer")

        Args:
            question: Natural language question.
            scope: Named scope filter as single string, comma-separated string,
                or list of scope strings (e.g. "research",
                "research, knowledge_systems",
                ["research", "research_small_llm"]).
            prefix: Source path prefix filter as a comma-separated string or
                list. Mutually exclusive with scope.
            deep: Use iterative retrieval for complex questions (default False).

        Returns:
            On success: {"status": "ok", "pipeline": "<pipeline used>",
                         "content_length": <int>, "duration_s": <float>,
                         "answer": "<grounded answer>",
                         "retrieval": {resolved_scope, scope_confidence, ...}}
            Unscoped calls also include ``scope_note`` when scope_source is
            ``default_scope`` or ``classifier``.
            On error:   {"error": "<message>"} (+ ``scope_note`` / ``retrieval`` when available)
        """
        pipeline = "rag-answer-deep" if deep else "rag-answer"
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
        if scope_override is not None:
            pipeline_options["scope_override"] = scope_override
        if prefixes is not None:
            pipeline_options["rag_source_prefixes"] = prefixes
        pipeline_options["include_retrieval_metadata"] = True

        t0 = monotonic_now()
        record_args: dict[str, Any] = {
            "pipeline": pipeline,
            "query": question,
            "scope": scope,
            "deep": deep,
        }
        if prefixes is not None:
            record_args["prefix"] = prefixes
        record("mcp.rag.pipeline.called", **record_args)

        rerank_model = pipeline_options.get("rerank_model", _RERANK_MODEL_DEFAULT)
        answer_model = pipeline_options.get("model", _ANSWER_MODEL_DEFAULT)
        pipeline_timeout = rag_pipeline_timeout(
            rerank_model
        ) + local_model_inference_timeout(answer_model)
        pipeline_options["timeout_seconds"] = pipeline_timeout

        try:
            result = _pipeline_call(
                pipeline,
                [{"role": "user", "content": question}],
                pipeline_options=pipeline_options,
                timeout=pipeline_timeout + _HTTP_BUFFER_S,
            )
        except httpx.TimeoutException as e:
            user_message = "Pipeline timed out. The question may be too complex — try without deep=True."
            return _handle_pipeline_error(e, pipeline, t0, user_message)
        except httpx.ConnectError as e:
            user_message = "Pipeline not available. Stargate may not be running."
            return _handle_pipeline_error(e, pipeline, t0, user_message)
        except httpx.HTTPStatusError as e:
            user_message = (
                f"Pipeline error: {e.response.status_code} {e.response.reason_phrase}"
            )
            return _handle_pipeline_error(e, pipeline, t0, user_message)
        except httpx.RequestError as e:
            user_message = f"Pipeline request failed: {e}"
            return _handle_pipeline_error(e, pipeline, t0, user_message)

        content = _extract_content(result) if result else ""
        duration = monotonic_now() - t0
        retrieval_fields = envelope_retrieval_fields(
            retrieval_metadata_from_response(result),
        )
        scope_note = _unscoped_scope_note(retrieval_fields) if unscoped else None

        if not content:
            record(
                "mcp.rag.pipeline.completed",
                pipeline=pipeline,
                duration_s=round(duration, 3),
                empty=True,
                query=question,
                scope=scope,
                prefix=prefixes,
                deep=deep,
            )
            return {
                "error": "Pipeline returned empty results.",
                **({"scope_note": scope_note} if scope_note else {}),
                **retrieval_fields,
            }

        logger.info(
            "rag_answer: question=%r pipeline=%s scope=%s prefix=%s → %d chars in %.1fs",
            question,
            pipeline,
            scope,
            prefixes,
            len(content),
            duration,
        )
        record(
            "mcp.rag.pipeline.completed",
            pipeline=pipeline,
            duration_s=round(duration, 3),
            content_length=len(content),
            scope=scope,
            prefix=prefixes,
            deep=deep,
        )
        return {
            "status": "ok",
            "pipeline": pipeline,
            "content_length": len(content),
            "duration_s": round(duration, 3),
            "answer": content,
            **({"scope_note": scope_note} if scope_note else {}),
            **retrieval_fields,
        }

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

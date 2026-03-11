"""RAG tools — pipeline-powered semantic search and grounded answers.

Routes queries through Stargate's RAG pipelines (rag-context, rag-answer,
rag-answer-deep) for multi-query rewriting, RRF merge, entity synthesis,
relevance gating, and optionally iterative retrieval.

Connectivity: MCP container → Stargate host on port 9999 via
host.docker.internal (extra_hosts in compose) or STARGATE_URL env override.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

import httpx
from mcp_events import monotonic_now, record

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_STARGATE_URL = os.environ.get("STARGATE_URL", "http://host.docker.internal:9999")
_CONTEXT_TIMEOUT = 90.0
_ANSWER_TIMEOUT = 180.0


def _pipeline_call(
    model: str,
    messages: list[dict[str, str]],
    *,
    pipeline_options: dict[str, Any] | None = None,
    timeout: float,
) -> dict[str, Any]:
    """POST to Stargate /v1/chat/completions. Raises httpx errors on failure."""
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        **({"pipeline_options": pipeline_options} if pipeline_options else {}),
    }

    url = f"{_STARGATE_URL}/v1/chat/completions"
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json=body)
        resp.raise_for_status()
        return resp.json()


def _handle_pipeline_error(
    exc: BaseException,
    pipeline: str,
    t0: float,
    user_message: str,
) -> dict[str, str]:
    """Log, record mcp.rag.pipeline.failed, and return error dict for HTTPX pipeline failures."""
    if isinstance(exc, httpx.TimeoutException):
        duration = monotonic_now() - t0
        logger.warning("Pipeline timed out after %.1fs: %s", duration, exc)
        record(
            "mcp.rag.pipeline.failed",
            pipeline=pipeline,
            error="timeout",
            duration_s=round(duration, 3),
        )
        return {"error": user_message}
    if isinstance(exc, httpx.ConnectError):
        logger.warning("Stargate connection failed: %s", exc)
        record("mcp.rag.pipeline.failed", pipeline=pipeline, error=str(exc))
        return {"error": user_message}
    if isinstance(exc, httpx.HTTPStatusError):
        logger.warning("Pipeline HTTP error: %s", exc)
        record(
            "mcp.rag.pipeline.failed",
            pipeline=pipeline,
            error=f"{exc.response.status_code}",
        )
        return {"error": user_message}
    logger.warning("Pipeline request error: %s", exc)
    record("mcp.rag.pipeline.failed", pipeline=pipeline, error=str(exc))
    return {"error": user_message}


def _extract_content(response: dict[str, Any]) -> str:
    """Extract message content from an OpenAI-format chat completions response."""
    choices = response.get("choices", [])
    if not choices:
        return ""
    return choices[0].get("message", {}).get("content", "")


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
        normalized = [s.strip() for s in scope if s.strip()]
        if not normalized:
            return None, "Invalid scope list: no scopes provided."
        return normalized, None

    scope_val = scope.strip()
    if not scope_val:
        return None, "Invalid scope: empty string."

    if "," in scope_val:
        normalized = [s.strip() for s in scope_val.split(",") if s.strip()]
        if not normalized:
            return None, "Invalid scope list: no scopes provided."
        return normalized, None

    return scope_val, None


def register_rag_tools(mcp: FastMCP) -> None:
    """Register RAG pipeline tools on *mcp*."""

    @mcp.tool()
    def rag_search(
        query: str,
        top_k: int = 20,
        scope: str | list[str] | None = None,
    ) -> dict[str, str]:
        """Search the knowledge base using the full RAG pipeline.

        Uses multi-query rewriting, reciprocal rank fusion, entity/relation
        merging, and property index boost — significantly richer than raw
        vector search.

        Available scopes: "project", "research", "all_research",
        "rag_systems", "code_retrieval", "workflows", "prompting",
        "knowledge_management", "graph_modeling", "temporal_provenance",
        "belief_consistency", "knowledge_systems", "both", "all".

        Args:
            query: Natural language search query.
            top_k: Maximum chunks after RRF merge (default 20).
            scope: Named scope filter as single string, comma-separated string,
                or list of scope strings (e.g. "research",
                "research, knowledge_management",
                ["all_research", "knowledge_management"]).

        Returns:
            On success: {"context": "<assembled context with source labels>",
                         "pipeline": "rag-context"}
            On error:   {"error": "<message>"}
        """
        pipeline_options: dict[str, Any] = {}
        if scope:
            scope_override, scope_error = _normalize_scope_override(scope)
            if scope_error:
                return {"error": scope_error}
            pipeline_options["scope_override"] = scope_override
        if top_k != 20:
            pipeline_options["rag_max_chunks"] = top_k

        t0 = monotonic_now()
        record(
            "mcp.rag.pipeline.called", pipeline="rag-context", query=query, scope=scope
        )

        try:
            result = _pipeline_call(
                "rag-context",
                [{"role": "user", "content": query}],
                pipeline_options=pipeline_options,
                timeout=_CONTEXT_TIMEOUT,
            )
        except (
            httpx.ConnectError,
            httpx.TimeoutException,
            httpx.HTTPStatusError,
            httpx.RequestError,
        ) as e:
            return _handle_pipeline_error(
                e,
                "rag-context",
                t0,
                (
                    "Pipeline timed out. The query may be too complex."
                    if isinstance(e, httpx.TimeoutException)
                    else "Pipeline not available. Stargate may not be running."
                    if isinstance(e, httpx.ConnectError)
                    else f"Pipeline error: {e.response.status_code} {e.response.reason_phrase}"
                    if isinstance(e, httpx.HTTPStatusError)
                    else f"Pipeline request failed: {e}"
                ),
            )

        content = _extract_content(result) if result else ""
        duration = monotonic_now() - t0

        if not content:
            record(
                "mcp.rag.pipeline.completed",
                pipeline="rag-context",
                duration_s=round(duration, 3),
                empty=True,
            )
            return {"error": "Pipeline returned empty results."}

        logger.info(
            "rag_search: query=%r scope=%s → %d chars in %.1fs",
            query,
            scope,
            len(content),
            duration,
        )
        record(
            "mcp.rag.pipeline.completed",
            pipeline="rag-context",
            duration_s=round(duration, 3),
            content_length=len(content),
        )
        return {"context": content, "pipeline": "rag-context"}

    @mcp.tool()
    def rag_answer(
        question: str,
        scope: str | list[str] | None = None,
        deep: bool = False,
    ) -> dict[str, str]:
        """Ask a question and get a grounded answer from the knowledge base.

        Uses the full RAG pipeline: query rewriting, multi-query retrieval,
        RRF merge, relevance gate, and answer generation. The answer is
        grounded in retrieved context — not hallucinated.

        Set deep=True for complex multi-faceted questions that benefit from
        iterative retrieval (up to 2 gap-filling passes).

        Available scopes: "project", "research", "all_research",
        "rag_systems", "code_retrieval", "workflows", "prompting",
        "knowledge_management", "graph_modeling", "temporal_provenance",
        "belief_consistency", "knowledge_systems", "both", "all".

        Args:
            question: Natural language question.
            scope: Named scope filter as single string, comma-separated string,
                or list of scope strings (e.g. "research",
                "research, knowledge_management",
                ["research", "knowledge_management"]).
            deep: Use iterative retrieval for complex questions (default False).

        Returns:
            On success: {"answer": "<grounded answer>", "pipeline": "<pipeline used>"}
            On error:   {"error": "<message>"}
        """
        pipeline = "rag-answer-deep" if deep else "rag-answer"
        pipeline_options: dict[str, Any] = {}
        if scope:
            scope_override, scope_error = _normalize_scope_override(scope)
            if scope_error:
                return {"error": scope_error}
            pipeline_options["scope_override"] = scope_override

        t0 = monotonic_now()
        record(
            "mcp.rag.pipeline.called",
            pipeline=pipeline,
            query=question,
            scope=scope,
            deep=deep,
        )

        try:
            result = _pipeline_call(
                pipeline,
                [{"role": "user", "content": question}],
                pipeline_options=pipeline_options,
                timeout=_ANSWER_TIMEOUT,
            )
        except (
            httpx.ConnectError,
            httpx.TimeoutException,
            httpx.HTTPStatusError,
            httpx.RequestError,
        ) as e:
            return _handle_pipeline_error(
                e,
                pipeline,
                t0,
                (
                    "Pipeline timed out. The question may be too complex — try without deep=True."
                    if isinstance(e, httpx.TimeoutException)
                    else "Pipeline not available. Stargate may not be running."
                    if isinstance(e, httpx.ConnectError)
                    else f"Pipeline error: {e.response.status_code} {e.response.reason_phrase}"
                    if isinstance(e, httpx.HTTPStatusError)
                    else f"Pipeline request failed: {e}"
                ),
            )

        content = _extract_content(result) if result else ""
        duration = monotonic_now() - t0

        if not content:
            record(
                "mcp.rag.pipeline.completed",
                pipeline=pipeline,
                duration_s=round(duration, 3),
                empty=True,
            )
            return {"error": "Pipeline returned empty results."}

        logger.info(
            "rag_answer: question=%r pipeline=%s → %d chars in %.1fs",
            question,
            pipeline,
            len(content),
            duration,
        )
        record(
            "mcp.rag.pipeline.completed",
            pipeline=pipeline,
            duration_s=round(duration, 3),
            content_length=len(content),
        )
        return {"answer": content, "pipeline": pipeline}

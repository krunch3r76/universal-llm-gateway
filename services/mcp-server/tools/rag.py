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
from typing import TYPE_CHECKING, Any, cast

import httpx
from mcp_events import monotonic_now, record

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_STARGATE_URL = os.environ.get("STARGATE_URL", "http://host.docker.internal:9999")
_CONTEXT_TIMEOUT = 90.0
_ANSWER_TIMEOUT = 180.0
_SCOPES_TIMEOUT = 15.0


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
        **({"pipeline_options": pipeline_options} if pipeline_options else {}),
    }

    url = f"{_STARGATE_URL}/v1/chat/completions"
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json=body)
        resp.raise_for_status()
        return resp.json()


def _rag_call(path: str, *, timeout: float) -> dict[str, Any]:
    """GET from Stargate passthrough and return parsed JSON."""
    url = f"{_STARGATE_URL.rstrip('/')}/{path.lstrip('/')}"
    with httpx.Client(timeout=timeout) as client:
        resp = client.get(url)
        resp.raise_for_status()
        payload_obj = cast(object, resp.json())
        if not isinstance(payload_obj, dict):
            raise ValueError("RAG response payload must be a JSON object")
        return cast(dict[str, Any], payload_obj)


def _handle_pipeline_error(
    exc: BaseException,
    pipeline: str,
    t0: float,
    user_message: str,
) -> dict[str, str]:
    """Log, record mcp.rag.pipeline.failed, and return error dict for HTTPX pipeline failures."""
    extra: dict[str, Any] = {}
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
    else:
        error_type = str(exc)
        log_message = f"Pipeline request error: {exc}"

    logger.warning(log_message, exc_info=True)
    record("mcp.rag.pipeline.failed", pipeline=pipeline, error=error_type, **extra)
    return {"error": user_message}


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

    normalized = [s.strip() for s in scope_val.split(",") if s.strip()]
    if not normalized:
        return None, "Invalid scope list: no scopes provided."
    if len(normalized) == 1:
        return normalized[0], None
    return normalized, None


def register_rag_tools(mcp: FastMCP) -> None:
    """Register RAG pipeline tools on *mcp*."""

    @mcp.tool()
    def rag_list_scopes() -> dict[str, object]:
        """List available retrieval scopes from the RAG scope registry.

        Returns:
            On success:
                {
                  "scopes": ["scope_a", "scope_b", ...],
                  "details": {
                    "scope_a": {"prefixes": [...], "description": "..."},
                    ...
                  }
                }
            On error: {"error": "<message>"}
        """
        t0 = monotonic_now()
        record("mcp.rag.scopes.called")
        try:
            payload = _rag_call("api/v1/rag/scopes", timeout=_SCOPES_TIMEOUT)
        except httpx.ConnectError as e:
            logger.warning("RAG scopes connection failed: %s", e)
            record("mcp.rag.scopes.failed", error=str(e))
            return {
                "error": (
                    "RAG scopes endpoint not reachable. Ensure RAG is running and "
                    "reachable through Stargate."
                )
            }
        except httpx.TimeoutException as e:
            logger.warning("RAG scopes request timed out: %s", e)
            record("mcp.rag.scopes.failed", error="timeout")
            return {"error": "RAG scopes request timed out."}
        except httpx.HTTPStatusError as e:
            logger.warning("RAG scopes HTTP error: %s", e)
            record("mcp.rag.scopes.failed", error=f"{e.response.status_code}")
            return {
                "error": (
                    f"RAG scopes endpoint error: "
                    f"{e.response.status_code} {e.response.reason_phrase}"
                )
            }
        except httpx.RequestError as e:
            logger.warning("RAG scopes request error: %s", e)
            record("mcp.rag.scopes.failed", error=str(e))
            return {"error": f"RAG scopes request failed: {e}"}
        except ValueError as e:
            logger.warning("RAG scopes invalid payload: %s", e)
            record("mcp.rag.scopes.failed", error="invalid_payload")
            return {"error": "RAG scopes endpoint returned invalid payload."}

        scopes_obj = payload.get("scopes", {})
        if not isinstance(scopes_obj, dict):
            record("mcp.rag.scopes.failed", error="invalid_payload")
            return {"error": "RAG scopes endpoint returned invalid payload."}

        scopes_typed = cast(dict[str, object], scopes_obj)
        scope_names = sorted(scopes_typed.keys())
        duration = monotonic_now() - t0
        record(
            "mcp.rag.scopes.completed",
            duration_s=round(duration, 3),
            count=len(scope_names),
        )
        return {"scopes": scope_names, "details": scopes_obj}

    @mcp.tool()
    def rag_search(
        query: str,
        top_k: int = 20,
        scope: str | list[str] | None = None,
    ) -> dict[str, str]:
        """Search the knowledge base and return raw context chunks.

        Returns assembled context with source labels for the agent to
        reason over. Prefer this over rag_answer when exploring a topic,
        gathering evidence for broader analysis, or when the question is
        open-ended. Use rag_answer instead when a direct synthesized
        answer is sufficient.

        Uses multi-query rewriting, reciprocal rank fusion, entity/relation
        merging, and property index boost.

        Call rag_list_scopes() for the current set of valid scope names.

        Args:
            query: Natural language search query.
            top_k: Maximum chunks after RRF merge (default 20).
            scope: Named scope filter as single string, comma-separated string,
                or list of scope strings (e.g. "research",
                "research, knowledge_systems",
                ["research_small_llm", "knowledge_systems"]).

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

        if not content:
            record(
                "mcp.rag.pipeline.completed",
                pipeline="rag-context",
                duration_s=round(duration, 3),
                empty=True,
                query=query,
                scope=scope,
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
        """Ask a specific question and get a grounded, synthesized answer.

        Prefer this over rag_search for direct factual or technical
        questions where a synthesized answer is the end goal. Use
        rag_search instead when you need raw context chunks to weave
        into broader reasoning or combine with non-RAG context.

        Has a relevance gate: returns empty if retrieved context doesn't
        directly address the question. Fall back to rag_search if this
        returns empty — it always returns whatever matches.

        Set deep=True for complex multi-faceted questions that benefit
        from iterative retrieval (up to 2 gap-filling passes).

        Call rag_list_scopes() for the current set of valid scope names.

        Args:
            question: Natural language question.
            scope: Named scope filter as single string, comma-separated string,
                or list of scope strings (e.g. "research",
                "research, knowledge_systems",
                ["research", "research_small_llm"]).
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

        if not content:
            record(
                "mcp.rag.pipeline.completed",
                pipeline=pipeline,
                duration_s=round(duration, 3),
                empty=True,
                query=question,
                scope=scope,
                deep=deep,
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

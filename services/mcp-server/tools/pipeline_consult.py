"""Pipeline consultation tool — expert advice on pipeline step issues.

Queries execution trace metadata from the event service, auto-detects
RAG scope from the model tier, and runs the consult-prompt-engineer
pipeline via Stargate to deliver grounded prompt improvement advice.
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any

import httpx
from mcp_events import monotonic_now, record

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_STARGATE_URL = os.environ.get("STARGATE_URL", "http://host.docker.internal:9999")
_CONSULT_TIMEOUT = 300.0
_CONSULT_PIPELINE = "consult-prompt-engineer"

# Available consult pipeline variants:
#   consult-prompt-engineer  — prompt/model output issues (code_review task, Devstral tier)
#   consult-architect        — system/retrieval architecture (strong_planner, excludes weak_grounding)
#   consult-researcher       — research/literature questions (general+reasoning, 128k ctx)
#   consult-planner          — planning/workflow questions
_CONSULT_PIPELINES = {
    "prompt-engineer": "consult-prompt-engineer",
    "architect": "consult-architect",
    "researcher": "consult-researcher",
    "planner": "consult-planner",
}

_QUERY_SOCKET = os.environ.get(
    "EVENT_QUERY_SOCKET", "/tmp/universal-protocol/events-query.sock"
)


def _query_event_service(body: dict[str, Any]) -> dict[str, Any]:
    """POST to event service query endpoint over UDS."""
    try:
        with httpx.Client(
            transport=httpx.HTTPTransport(uds=_QUERY_SOCKET),
            timeout=10.0,
        ) as client:
            resp = client.post("http://localhost/v1/query", json=body)
            resp.raise_for_status()
            return resp.json()
    except httpx.RequestError as e:
        logger.error("Event service request failed: %s", e, exc_info=True)
        return {"error": f"Event service request failed: {e}"}
    except Exception as e:
        logger.error("Event service query failed: %s", e, exc_info=True)
        return {"error": f"Event service query failed: {e}"}


def _extract_step_metadata(execution_id: str, step_name: str) -> dict[str, Any]:
    """Query event service for step metadata via parameterized SQL.

    Returns a dict with model, step_type, duration_seconds, pipeline_id,
    prompt_tokens, completion_tokens. Empty dict on failure.
    """
    sql = (
        "SELECT payload FROM events "
        "WHERE execution_id = ? "
        "AND signal IN ("
        "  'pipeline.step.started', "
        "  'pipeline.step.completed', "
        "  'pipeline.step.model.resolved'"
        ") "
        "ORDER BY seq"
    )
    body: dict[str, Any] = {
        "type": "sql",
        "sql": sql,
        "params": [execution_id],
        "limit": 200,
    }
    result = _query_event_service(body)
    if "error" in result:
        logger.warning("Failed to query step metadata: %s", result["error"])
        return {}

    meta: dict[str, Any] = {}
    for row in result.get("rows", []):
        payload_raw = row.get("payload", "")
        if not payload_raw:
            continue
        try:
            payload = (
                json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
            )
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(
                "Failed to parse event payload: %s, raw: %.200s", e, payload_raw
            )
            continue

        if payload.get("step_name") != step_name:
            continue

        if "pipeline_id" in payload:
            meta["pipeline_id"] = payload["pipeline_id"]
        if "step_type" in payload:
            meta["step_type"] = payload["step_type"]
        if "model_id" in payload:
            meta["model"] = payload["model_id"]
        if "duration_seconds" in payload:
            meta["duration_seconds"] = payload["duration_seconds"]
        if "prompt_tokens" in payload:
            meta["prompt_tokens"] = payload["prompt_tokens"]
        if "completion_tokens" in payload:
            meta["completion_tokens"] = payload["completion_tokens"]

    return meta


def _detect_scope(model_id: str | None) -> str:
    """Auto-detect RAG scope from model tier.

    Models with '/' in their ID (e.g. cloud) → research (frontier prompting).
    Local models (no '/' in ID) → research_small_llm (small model techniques).
    If model_id is None → research (broader coverage).
    """
    if model_id is None:
        return "research"
    if "/" in model_id:
        return "research"
    return "research_small_llm"


def register_pipeline_consult_tools(mcp: FastMCP) -> None:
    """Register pipeline consultation tool on *mcp*."""

    @mcp.tool()
    def pipeline_consult(
        execution_id: str,
        step_name: str,
        problem: str,
        scope: str | None = None,
        pipeline: str | None = None,
    ) -> dict[str, Any]:
        """Get RAG-grounded expert advice from a frontier model.

        Default variant (prompt-engineer) analyzes pipeline step issues.
        Other variants handle system architecture, research questions,
        and planning — not limited to pipeline steps.

        Queries the execution trace for step metadata, auto-detects the
        RAG scope from the model tier, and runs a consult pipeline via
        Stargate with grounded research context.

        The 'problem' field should include as much context as possible:
        prompt text, model output, and a description of what's wrong
        or what you need advice on.

        Args:
            execution_id: Pipeline execution ID (from pipeline_run result).
            step_name: Name of the step to consult about.
            problem: Detailed description of the issue, ideally including
                     prompt text and model output excerpts.
            scope: Override auto-detected RAG scope (e.g. 'research',
                   'research_small_llm', 'workflows').
            pipeline: Consult pipeline variant to use. One of:
                      'prompt-engineer' (default) — prompt/output issues,
                        code_review task, routes to code-focused models;
                      'architect' — system/retrieval architecture questions,
                        strong_planner models, excludes weak_grounding;
                      'researcher' — research/literature questions,
                        general+reasoning models, 128k context;
                      'planner' — planning/workflow questions.

        Returns:
            On success: {"advice": "...", "scope_used": "...", "model": "...",
                         "execution_id": "...", "duration_s": <float>}
            On error: {"error": "<message>"}
        """
        t0 = monotonic_now()
        record(
            "mcp.pipeline.consult.called",
            execution_id=execution_id,
            step_name=step_name,
            pipeline=pipeline or "prompt-engineer",
        )

        consult_pipeline = (
            _CONSULT_PIPELINES.get(pipeline, _CONSULT_PIPELINE)
            if pipeline
            else _CONSULT_PIPELINE
        )

        step_meta = _extract_step_metadata(execution_id, step_name)

        effective_scope = scope
        if effective_scope is None:
            effective_scope = _detect_scope(step_meta.get("model"))
        pipeline_id = step_meta.get("pipeline_id", "unknown")

        context_sections: list[str] = [
            f"## Pipeline Step: {step_name}",
            f"Pipeline: {pipeline_id}, Execution: {execution_id}",
        ]
        if step_meta.get("model"):
            context_sections.append(f"Model: {step_meta['model']}")
        if step_meta.get("step_type"):
            context_sections.append(f"Step type: {step_meta['step_type']}")
        if step_meta.get("duration_seconds") is not None:
            context_sections.append(f"Duration: {step_meta['duration_seconds']:.1f}s")
        if step_meta.get("prompt_tokens") or step_meta.get("completion_tokens"):
            context_sections.append(
                f"Tokens: {step_meta.get('prompt_tokens', 0)} in / "
                f"{step_meta.get('completion_tokens', 0)} out"
            )

        context_sections.append(f"\n## Problem\n{problem}")
        context_sections.append(
            "## Your Task\n"
            "1. What specific issues do you see given the problem description?\n"
            "2. What changes to the system prompt and/or user prompt would fix them?\n"
            "3. Provide the exact revised prompt text for each change you recommend."
        )

        user_message = "\n".join(context_sections)

        body: dict[str, Any] = {
            "model": consult_pipeline,
            "messages": [{"role": "user", "content": user_message}],
        }
        if effective_scope:
            body["pipeline_options"] = {"scope_override": effective_scope}

        try:
            url = f"{_STARGATE_URL}/v1/chat/completions"
            with httpx.Client(timeout=_CONSULT_TIMEOUT) as client:
                resp = client.post(url, json=body)
                resp.raise_for_status()
                data = resp.json()
        except httpx.TimeoutException:
            duration = monotonic_now() - t0
            record(
                "mcp.pipeline.consult.failed",
                execution_id=execution_id,
                step_name=step_name,
                error="timeout",
                duration_s=round(duration, 3),
            )
            return {"error": f"Consultation timed out after {_CONSULT_TIMEOUT}s."}
        except httpx.ConnectError as e:
            record(
                "mcp.pipeline.consult.failed",
                execution_id=execution_id,
                step_name=step_name,
                error=str(e),
            )
            return {"error": f"Stargate not reachable: {e}"}
        except httpx.HTTPStatusError as e:
            record(
                "mcp.pipeline.consult.failed",
                execution_id=execution_id,
                step_name=step_name,
                error=f"{e.response.status_code}",
            )
            return {
                "error": (
                    f"Consultation pipeline error: "
                    f"{e.response.status_code} {e.response.reason_phrase}"
                )
            }

        content = ""
        choices = data.get("choices", [])
        if choices:
            content = choices[0].get("message", {}).get("content", "")

        duration = monotonic_now() - t0
        consult_exec_id = resp.headers.get("x-pipeline-execution-id", "")

        if not content:
            record(
                "mcp.pipeline.consult.completed",
                execution_id=execution_id,
                step_name=step_name,
                duration_s=round(duration, 3),
                empty=True,
            )
            return {"error": "Consultation returned empty results."}

        record(
            "mcp.pipeline.consult.completed",
            execution_id=execution_id,
            step_name=step_name,
            duration_s=round(duration, 3),
            content_length=len(content),
        )

        result: dict[str, Any] = {
            "advice": content,
            "scope_used": effective_scope,
            "model": data.get("model", _CONSULT_PIPELINE),
            "duration_s": round(duration, 3),
        }
        if consult_exec_id:
            result["execution_id"] = consult_exec_id
        return result

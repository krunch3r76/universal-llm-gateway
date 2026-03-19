"""Pipeline tools — run pipelines and validate configurations.

Enables agents to trigger pipeline execution via Stargate HTTP,
block until completion, and retrieve execution traces. Provides
empirical feedback for the pipeline iteration loop.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

import httpx
from mcp_events import monotonic_now, record
from transport_utils import make_sync_client

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_STARGATE_URL = os.environ.get("STARGATE_URL", "http://host.docker.internal:9999")
_RUN_TIMEOUT_FALLBACK = 480.0
_TIMEOUT_BUFFER = 30.0
_VALIDATE_TIMEOUT = 15.0

_QUERY_SOCKET = os.environ.get(
    "EVENT_QUERY_SOCKET", "/tmp/universal-protocol/events-query.sock"
)

_pipeline_timeouts: dict[str, float] = {}
_last_timeout_refresh_monotonic = 0.0
_TIMEOUT_CACHE_TTL_S = 60.0


def _fetch_pipelines_metadata() -> dict[str, Any]:
    """Fetch pipeline registry metadata from Stargate.

    Returns:
        Decoded JSON payload from ``/api/v1/pipelines``.
    """
    url = f"{_STARGATE_URL}/api/v1/pipelines"
    try:
        with httpx.Client(timeout=_VALIDATE_TIMEOUT) as client:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        logger.warning("Failed to fetch pipelines metadata from %s", url, exc_info=True)
        raise


def _cache_pipeline_timeouts(pipelines: dict[str, Any]) -> None:
    """Refresh the local timeout cache from a pipelines metadata mapping.

    Args:
        pipelines: Mapping of pipeline_id -> metadata payload from Stargate.
    """
    _pipeline_timeouts.clear()
    for pid, info in pipelines.items():
        ts = info.get("timeout_seconds")
        if isinstance(ts, int | float) and ts > 0:
            _pipeline_timeouts[pid] = float(ts)


def _refresh_pipeline_timeouts() -> None:
    """Refresh cached pipeline timeouts from the live Stargate registry.

    Keeps the previous cache untouched when the metadata fetch fails so callers
    can still use the last known timeout values instead of silently clearing the
    cache and forcing the hardcoded fallback for every pipeline.
    """
    global _last_timeout_refresh_monotonic
    try:
        data = _fetch_pipelines_metadata()
    except Exception as exc:
        logger.warning(
            "Pipeline metadata fetch failed; using cached/fallback timeouts: %s",
            exc,
        )
        return

    pipelines = data.get("pipelines", {})
    if not isinstance(pipelines, dict):
        logger.warning(
            "Pipeline metadata fetch returned unexpected payload shape: %r",
            type(pipelines).__name__,
        )
        return
    _cache_pipeline_timeouts(pipelines)
    _last_timeout_refresh_monotonic = monotonic_now()


def _resolve_timeout(pipeline: str, explicit: float | None) -> float:
    """Determine effective HTTP timeout for a pipeline_run call.

    Priority: explicit caller override > auto-detected from registry > fallback.
    Adds _TIMEOUT_BUFFER to pipeline-configured timeouts for HTTP overhead.
    """
    if explicit is not None:
        return explicit

    cache_age = monotonic_now() - _last_timeout_refresh_monotonic
    if not _pipeline_timeouts or cache_age > _TIMEOUT_CACHE_TTL_S:
        _refresh_pipeline_timeouts()

    configured = _pipeline_timeouts.get(pipeline)
    if configured is not None:
        return configured + _TIMEOUT_BUFFER

    return _RUN_TIMEOUT_FALLBACK


def _query_event_service(body: dict[str, Any]) -> dict[str, Any]:
    """POST a structured query to the event-service UDS endpoint.

    Returns the decoded JSON response on success. On transport or HTTP failure,
    logs a warning and returns an error dictionary so MCP callers receive a
    structured failure instead of an uncaught exception.
    """
    try:
        with make_sync_client(f"unix://{_QUERY_SOCKET}", timeout=10.0) as client:
            resp = client.post("/v1/query", json=body)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as exc:
        logger.warning("Event service HTTP query failed: %s", exc)
        return {"error": f"Event service HTTP query failed: {exc}"}
    except ValueError as exc:
        logger.warning("Event service query returned invalid JSON: %s", exc)
        return {"error": f"Event service query returned invalid JSON: {exc}"}
    except Exception as exc:
        logger.warning("Event service query failed: %s", exc)
        return {"error": f"Event service query failed: {exc}"}


def _validate_error(pipeline: str, message: str) -> dict[str, Any]:
    """Build a consistent error shape for validate_pipeline failures."""
    return {
        "valid": False,
        "pipeline": pipeline,
        "errors": [message],
        "steps": 0,
        "models": [],
        "domain": "",
    }


def register_pipeline_tools(mcp: FastMCP) -> None:
    """Register pipeline execution and validation tools."""
    _refresh_pipeline_timeouts()

    @mcp.tool()
    def pipeline_run(
        pipeline: str,
        messages: list[dict[str, str]],
        options: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Run a pipeline and return execution summary with trace.

        Blocks until the pipeline completes. Returns the response content
        plus execution metadata. Use query_observability with
        operation='pipeline-trace' for detailed step-by-step traces.

        Pipeline YAML, prompts, and model configs hot-reload on file
        change (~2s debounce) — no service restart needed between runs.

        The HTTP timeout is auto-detected from the pipeline's configured
        timeout_seconds (via Stargate registry), so callers don't need to
        know each pipeline's budget. Falls back to 480s if metadata is
        unavailable.

        Args:
            pipeline: Pipeline ID (e.g. 'consensus', 'rag-context').
            messages: Chat messages in OpenAI format.
            options: Optional pipeline_options dict.
            timeout: Override HTTP timeout in seconds. Auto-detected from
                pipeline registry when not provided.

        Returns:
            On success: {
                "content": "<response>",
                "model": "<pipeline ID>",
                "execution_id": "<from response headers if available>",
                "duration_s": <float>,
                "usage": {...}
            }
            On error: {"error": "<message>"}
        """
        t0 = monotonic_now()
        record("mcp.pipeline.run.called", pipeline=pipeline)

        effective_timeout = _resolve_timeout(pipeline, timeout)
        body: dict[str, Any] = {"model": pipeline, "messages": messages}
        if options:
            body["pipeline_options"] = options

        try:
            url = f"{_STARGATE_URL}/v1/chat/completions"
            with httpx.Client(timeout=effective_timeout) as client:
                resp = client.post(url, json=body)
                resp.raise_for_status()
                data = resp.json()
        except httpx.TimeoutException:
            duration = monotonic_now() - t0
            record(
                "mcp.pipeline.run.failed",
                pipeline=pipeline,
                error="timeout",
                duration_s=round(duration, 3),
            )
            return {
                "error": f"Pipeline '{pipeline}' timed out after {effective_timeout}s."
            }
        except httpx.ConnectError as e:
            record("mcp.pipeline.run.failed", pipeline=pipeline, error="connect_error")
            return {"error": f"Stargate not reachable: {e}"}
        except httpx.HTTPStatusError as e:
            record(
                "mcp.pipeline.run.failed",
                pipeline=pipeline,
                error=f"{e.response.status_code}",
            )
            return {
                "error": f"Pipeline error: {e.response.status_code} {e.response.reason_phrase}"
            }

        duration = monotonic_now() - t0

        content = ""
        choices = data.get("choices", [])
        if choices:
            content = choices[0].get("message", {}).get("content", "")

        execution_id = resp.headers.get("x-pipeline-execution-id", "")

        result: dict[str, Any] = {
            "content": content,
            "model": data.get("model", pipeline),
            "duration_s": round(duration, 3),
        }
        if execution_id:
            result["execution_id"] = execution_id
        if "usage" in data:
            result["usage"] = data["usage"]

        record(
            "mcp.pipeline.run.completed",
            pipeline=pipeline,
            duration_s=round(duration, 3),
            content_length=len(content),
        )
        return result

    @mcp.tool()
    def validate_pipeline(pipeline: str) -> dict[str, Any]:
        """Validate pipeline YAML structure and model availability.

        Checks that the pipeline exists, YAML is valid, required models
        are available, and prompt references resolve. Does NOT consume
        inference compute.

        Args:
            pipeline: Pipeline ID to validate.

        Returns:
            On success: {"valid": true, "pipeline": "<id>", "steps": <count>}
            On error: {"valid": false, "errors": ["..."]}
        """
        t0 = monotonic_now()
        record("mcp.pipeline.validate.called", pipeline=pipeline)

        try:
            pipelines_data = _fetch_pipelines_metadata()
        except httpx.ConnectError as e:
            logger.warning(
                "Stargate connection failed during pipeline validation for %s: %s",
                pipeline,
                e,
            )
            return _validate_error(pipeline, f"Stargate not reachable: {e}")
        except httpx.HTTPStatusError as e:
            logger.warning(
                "HTTP status error during pipeline validation for %s: %s",
                pipeline,
                e.response.status_code,
            )
            return _validate_error(
                pipeline,
                f"Pipeline API error: {e.response.status_code}",
            )
        except Exception as e:
            logger.exception(
                "Unexpected error during pipeline validation for %s", pipeline
            )
            return _validate_error(pipeline, f"Validation failed: {e}")

        pipelines = pipelines_data.get("pipelines", {})
        if not isinstance(pipelines, dict):
            logger.error(
                "Pipeline metadata endpoint returned invalid pipelines payload: %r",
                type(pipelines).__name__,
            )
            return _validate_error(
                pipeline,
                "Pipeline API returned invalid metadata payload.",
            )

        _cache_pipeline_timeouts(pipelines)

        if pipeline not in pipelines:
            available = sorted(pipelines.keys())
            return _validate_error(
                pipeline,
                f"Pipeline '{pipeline}' not found. Available: {available}",
            )

        info = pipelines[pipeline]
        duration = monotonic_now() - t0
        record(
            "mcp.pipeline.validate.completed",
            pipeline=pipeline,
            duration_s=round(duration, 3),
        )

        return {
            "valid": True,
            "pipeline": pipeline,
            "errors": [],
            "steps": info.get("steps", 0),
            "models": info.get("models", []),
            "domain": info.get("domain", ""),
        }

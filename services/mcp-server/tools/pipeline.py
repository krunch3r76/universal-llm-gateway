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
from mcp_toolprogress import toolprogress_begin, toolprogress_end, toolprogress_phase
from transport_utils import make_sync_client

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_STARGATE_URL = os.environ.get("STARGATE_URL", "http://io:9999")
_RUN_TIMEOUT_FALLBACK = 480.0
_TIMEOUT_BUFFER = 30.0
_VALIDATE_TIMEOUT = 15.0

_QUERY_SOCKET = os.environ.get(
    "EVENTS_QUERY_SOCK", "/tmp/universal-protocol/events-query.sock"
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
    """Determine effective HTTP timeout for a pipeline call.

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
    def pipeline(
        pipeline: str,
        messages: list[dict[str, str]],
        options: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Run a pipeline, block until done, return result with execution_id.

        Full docs: fs(op="md_read", sandbox="project", path="universal-llm-gateway/docs/tool-reference.md", section="pipeline")
        """
        t0 = monotonic_now()
        record("mcp.pipeline.run.called", pipeline=pipeline)
        tp_t0, tp_timer = toolprogress_begin("pipeline", pipeline=pipeline)

        effective_timeout = _resolve_timeout(pipeline, timeout)
        body: dict[str, Any] = {"model": pipeline, "messages": messages}
        if options:
            body["pipeline_options"] = options

        tp_err: str | None = None
        tp_exec_id: str | None = None
        try:
            toolprogress_phase("pipeline", "stargate_post_begin", pipeline=pipeline)
            url = f"{_STARGATE_URL}/v1/chat/completions"
            with httpx.Client(timeout=effective_timeout) as client:
                resp = client.post(url, json=body)
                resp.raise_for_status()
                data = resp.json()
            toolprogress_phase("pipeline", "stargate_post_done", pipeline=pipeline)

            duration = monotonic_now() - t0
            content = ""
            choices = data.get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content", "")

            tp_exec_id = resp.headers.get("x-pipeline-execution-id", "") or None

            result: dict[str, Any] = {
                "content": content,
                "model": data.get("model", pipeline),
                "duration_s": round(duration, 3),
            }
            if tp_exec_id:
                result["execution_id"] = tp_exec_id
            if "usage" in data:
                result["usage"] = data["usage"]

            record(
                "mcp.pipeline.run.completed",
                pipeline=pipeline,
                duration_s=round(duration, 3),
                content_length=len(content),
            )
            return result
        except httpx.TimeoutException:
            duration = monotonic_now() - t0
            tp_err = "timeout"
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
            tp_err = "connect_error"
            record("mcp.pipeline.run.failed", pipeline=pipeline, error="connect_error")
            return {"error": f"Stargate not reachable: {e}"}
        except httpx.HTTPStatusError as e:
            tp_err = f"http_{e.response.status_code}"
            record(
                "mcp.pipeline.run.failed",
                pipeline=pipeline,
                error=f"{e.response.status_code}",
            )
            result = {
                "error": f"Pipeline error: {e.response.status_code} {e.response.reason_phrase}"
            }
            try:
                detail = e.response.text.strip()
            except Exception as exc:
                logger.warning("Failed reading pipeline error response body: %s", exc)
            else:
                if detail:
                    result["detail"] = detail[:500]
            return result
        except Exception as exc:
            tp_err = str(exc)
            raise
        finally:
            toolprogress_end(
                tp_t0,
                tp_timer,
                "pipeline",
                error=tp_err,
                pipeline=pipeline,
                execution_id=tp_exec_id,
            )

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

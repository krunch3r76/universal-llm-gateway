"""Pipeline tool — unified ``pipeline(op, …)`` primary MCP surface.

Single primary tool dispatching by
``op ∈ {run, async, result, validate, stats, cancel}``
to per-op private handlers. Enables agents to run pipelines synchronously,
dispatch async jobs, fetch results, and validate configs — all through
one first-class schema.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Literal

import httpx
from mcp_events import monotonic_now, record
from mcp_toolprogress import toolprogress_begin, toolprogress_end, toolprogress_phase
from transport_utils import make_sync_client
from universal_logging import get_logger

from ._restart_probe import annotate_unreachable_error

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = get_logger(__name__)

STARGATE_URL = os.environ.get("STARGATE_URL", "http://io:9999")
_RUN_TIMEOUT_FALLBACK = 480.0
_TIMEOUT_BUFFER = 30.0
_VALIDATE_TIMEOUT = 15.0
_DISPATCH_TIMEOUT = 15.0
_RESULT_MAX_WAIT = 60.0
_RESULT_POLL_BUFFER = 15.0

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
    url = "/api/v1/pipelines"
    try:
        with make_sync_client(STARGATE_URL, timeout=_VALIDATE_TIMEOUT) as client:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        logger.warning(
            "Failed to fetch pipelines metadata from %s/%s",
            STARGATE_URL,
            url,
            exc_info=True,
        )
        raise


def _cache_pipeline_timeouts(pipelines: dict[str, Any]) -> None:
    """Refresh the local timeout cache from a pipelines metadata mapping."""
    _pipeline_timeouts.clear()
    for pid, info in pipelines.items():
        ts = info.get("timeout_seconds")
        if isinstance(ts, int | float) and ts > 0:
            _pipeline_timeouts[pid] = float(ts)


def _refresh_pipeline_timeouts() -> None:
    """Refresh cached pipeline timeouts from the live Stargate registry."""
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


def resolve_timeout(pipeline_id: str, explicit: float | None) -> float:
    """Determine effective HTTP timeout for a pipeline call."""
    if explicit is not None:
        return explicit

    cache_age = monotonic_now() - _last_timeout_refresh_monotonic
    if not _pipeline_timeouts or cache_age > _TIMEOUT_CACHE_TTL_S:
        _refresh_pipeline_timeouts()

    configured = _pipeline_timeouts.get(pipeline_id)
    if configured is not None:
        return configured + _TIMEOUT_BUFFER

    return _RUN_TIMEOUT_FALLBACK


def _query_event_service(body: dict[str, Any]) -> dict[str, Any]:
    """POST a structured query to the event-service UDS endpoint."""
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


def _pipeline_metadata_from_response(data: dict[str, Any]) -> dict[str, Any] | None:
    """Return Stargate ``pipeline`` block when present (e.g. ``retrieval`` metadata)."""
    pipeline = data.get("pipeline")
    return pipeline if isinstance(pipeline, dict) and pipeline else None


def _validate_error(pipeline_id: str, message: str) -> dict[str, Any]:
    """Build a consistent error shape for validate-op failures."""
    return {
        "valid": False,
        "pipeline": pipeline_id,
        "errors": [message],
        "steps": 0,
        "models": [],
        "domain": "",
    }


def _pipeline_run(
    pipeline_id: str,
    messages: list[dict[str, str]],
    options: dict[str, Any] | None,
    timeout: float | None,
) -> dict[str, Any]:
    t0 = monotonic_now()
    record("mcp.pipeline.run.called", pipeline=pipeline_id)
    tp_t0, tp_timer = toolprogress_begin("pipeline", pipeline=pipeline_id)

    effective_timeout = resolve_timeout(pipeline_id, timeout)
    body: dict[str, Any] = {"model": pipeline_id, "messages": messages}
    if options:
        body["pipeline_options"] = options

    tp_err: str | None = None
    tp_exec_id: str | None = None
    try:
        toolprogress_phase("pipeline", "stargate_post_begin", pipeline=pipeline_id)
        url = "/v1/chat/completions"
        with make_sync_client(STARGATE_URL, timeout=effective_timeout) as client:
            resp = client.post(url, json=body)
            resp.raise_for_status()
            data = resp.json()
        toolprogress_phase("pipeline", "stargate_post_done", pipeline=pipeline_id)

        duration = monotonic_now() - t0
        content = ""
        choices = data.get("choices", [])
        if choices:
            content = choices[0].get("message", {}).get("content", "")

        tp_exec_id = resp.headers.get("x-pipeline-execution-id", "") or None

        result: dict[str, Any] = {
            "content": content,
            "model": data.get("model", pipeline_id),
            "duration_s": round(duration, 3),
        }
        if tp_exec_id:
            result["execution_id"] = tp_exec_id
        if "usage" in data:
            result["usage"] = data["usage"]
        pipeline_meta = _pipeline_metadata_from_response(data)
        if pipeline_meta is not None:
            result["pipeline"] = pipeline_meta

        record(
            "mcp.pipeline.run.completed",
            pipeline=pipeline_id,
            duration_s=round(duration, 3),
            content_length=len(content),
        )
        return result
    except httpx.TimeoutException:
        duration = monotonic_now() - t0
        tp_err = "timeout"
        record(
            "mcp.pipeline.run.failed",
            pipeline=pipeline_id,
            error="timeout",
            duration_s=round(duration, 3),
        )
        return {
            "error": f"Pipeline '{pipeline_id}' timed out after {effective_timeout}s."
        }
    except httpx.ConnectError as e:
        tp_err = "connect_error"
        record("mcp.pipeline.run.failed", pipeline=pipeline_id, error="connect_error")
        return annotate_unreachable_error(
            code="stargate_unreachable",
            message=f"Stargate not reachable: {e}",
            service="stargate",
            flat_error=True,
        )
    except httpx.HTTPStatusError as e:
        tp_err = f"http_{e.response.status_code}"
        record(
            "mcp.pipeline.run.failed",
            pipeline=pipeline_id,
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
            pipeline=pipeline_id,
            execution_id=tp_exec_id,
        )


def _pipeline_async(
    pipeline_id: str,
    messages: list[dict[str, str]],
    options: dict[str, Any] | None,
    result_delivery: dict[str, Any] | None,
    caller_agent: str | None,
) -> dict[str, Any]:
    t0 = monotonic_now()
    record("mcp.pipeline.async.called", pipeline=pipeline_id)

    body: dict[str, Any] = {"model": pipeline_id, "messages": messages}
    if options:
        body["pipeline_options"] = options
    if result_delivery:
        body["result_delivery"] = result_delivery
    if caller_agent:
        body["caller_agent"] = caller_agent

    url = "/api/v1/pipelines/dispatch"
    try:
        with make_sync_client(STARGATE_URL, timeout=_DISPATCH_TIMEOUT) as client:
            resp = client.post(url, json=body)
        if resp.status_code >= 400:
            try:
                payload = resp.json()
            except ValueError:
                payload = {
                    "error": {
                        "code": f"http_{resp.status_code}",
                        "message": resp.text[:500],
                    }
                }
            record(
                "mcp.pipeline.async.failed",
                pipeline=pipeline_id,
                status_code=resp.status_code,
            )
            return payload if isinstance(payload, dict) else {"error": payload}

        data = resp.json()
        record(
            "mcp.pipeline.async.dispatched",
            pipeline=pipeline_id,
            execution_id=data.get("execution_id", ""),
            duration_s=round(monotonic_now() - t0, 3),
        )
        return data
    except httpx.ConnectError as exc:
        record("mcp.pipeline.async.failed", pipeline=pipeline_id, error="connect_error")
        return annotate_unreachable_error(
            code="stargate_unreachable",
            message=f"Stargate not reachable: {exc}",
            service="stargate",
        )
    except httpx.HTTPError as exc:
        record("mcp.pipeline.async.failed", pipeline=pipeline_id, error=str(exc))
        return {"error": {"code": "http_error", "message": str(exc)}}


def _pipeline_stats() -> dict[str, Any]:
    """Fetch tracker occupancy snapshot from Stargate."""
    url = "/api/v1/pipelines/dispatch/stats"
    try:
        with make_sync_client(STARGATE_URL, timeout=_VALIDATE_TIMEOUT) as client:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as exc:
        return {"error": {"code": "stargate_http_error", "message": str(exc)}}


def _pipeline_cancel(execution_id: str) -> dict[str, Any]:
    """Cancel an in-flight async-dispatched execution."""
    url = f"/api/v1/pipelines/executions/{execution_id}"
    try:
        with make_sync_client(STARGATE_URL, timeout=_DISPATCH_TIMEOUT) as client:
            resp = client.delete(url)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as exc:
        return {"error": {"code": "stargate_http_error", "message": str(exc)}}


def _pipeline_result(execution_id: str, wait_seconds: float) -> dict[str, Any]:
    record("mcp.pipeline.result.called", execution_id=execution_id)

    wait_clamped = max(0.0, min(wait_seconds, _RESULT_MAX_WAIT))
    http_timeout = wait_clamped + _RESULT_POLL_BUFFER

    url = f"/api/v1/pipelines/executions/{execution_id}"
    try:
        with make_sync_client(STARGATE_URL, timeout=http_timeout) as client:
            resp = client.get(url, params={"wait": wait_clamped})
        if resp.status_code >= 400:
            try:
                return resp.json()
            except ValueError:
                return {
                    "error": {
                        "code": f"http_{resp.status_code}",
                        "message": resp.text[:500],
                    }
                }
        return resp.json()
    except httpx.ConnectError as exc:
        return annotate_unreachable_error(
            code="stargate_unreachable",
            message=f"Stargate not reachable: {exc}",
            service="stargate",
        )
    except httpx.HTTPError as exc:
        return {"error": {"code": "http_error", "message": str(exc)}}


def _pipeline_validate(pipeline_id: str) -> dict[str, Any]:
    t0 = monotonic_now()
    record("mcp.pipeline.validate.called", pipeline=pipeline_id)

    try:
        pipelines_data = _fetch_pipelines_metadata()
    except httpx.ConnectError as e:
        logger.warning(
            "Stargate connection failed during pipeline validation for %s: %s",
            pipeline_id,
            e,
        )
        return _validate_error(pipeline_id, f"Stargate not reachable: {e}")
    except httpx.HTTPStatusError as e:
        logger.warning(
            "HTTP status error during pipeline validation for %s: %s",
            pipeline_id,
            e.response.status_code,
        )
        return _validate_error(
            pipeline_id,
            f"Pipeline API error: {e.response.status_code}",
        )
    except Exception as e:
        logger.exception(
            "Unexpected error during pipeline validation for %s", pipeline_id
        )
        return _validate_error(pipeline_id, f"Validation failed: {e}")

    pipelines = pipelines_data.get("pipelines", {})
    if not isinstance(pipelines, dict):
        logger.error(
            "Pipeline metadata endpoint returned invalid pipelines payload: %r",
            type(pipelines).__name__,
        )
        return _validate_error(
            pipeline_id,
            "Pipeline API returned invalid metadata payload.",
        )

    _cache_pipeline_timeouts(pipelines)

    if pipeline_id not in pipelines:
        available = sorted(pipelines.keys())
        return _validate_error(
            pipeline_id,
            f"Pipeline '{pipeline_id}' not found. Available: {available}",
        )

    info = pipelines[pipeline_id]
    duration = monotonic_now() - t0
    record(
        "mcp.pipeline.validate.completed",
        pipeline=pipeline_id,
        duration_s=round(duration, 3),
    )

    return {
        "valid": True,
        "pipeline": pipeline_id,
        "errors": [],
        "steps": info.get("steps", 0),
        "models": info.get("models", []),
        "domain": info.get("domain", ""),
    }


def register_pipeline_tools(mcp: FastMCP) -> None:
    """Register the unified ``pipeline`` tool."""
    _refresh_pipeline_timeouts()

    @mcp.tool(title="Pipeline")
    def pipeline(
        op: Literal["run", "async", "result", "validate", "stats", "cancel"],
        pipeline_id: str | None = None,
        messages: list[dict[str, str]] | None = None,
        execution_id: str | None = None,
        options: dict[str, Any] | None = None,
        timeout: float | None = None,
        result_delivery: dict[str, Any] | None = None,
        caller_agent: str | None = None,
        wait_seconds: float = 0.0,
    ) -> dict[str, Any]:
        """Pipeline execution and inspection — dispatches by ``op``.

        Ops:

        - ``"run"`` — sync block until pipeline completes. Returns
          ``{content, model, duration_s, execution_id?, usage?, pipeline?}``.
          ``pipeline`` mirrors Stargate when present (e.g. ``retrieval`` when
          ``options.include_retrieval_metadata`` is set). Required:
          ``pipeline_id``, ``messages``. Optional: ``options``, ``timeout``
          (auto-detected from pipeline config when omitted). Hot-reload:
          YAML/prompts/models reload on file change.

        - ``"async"`` — async dispatch; returns ``execution_id`` immediately.
          Required: ``pipeline_id``, ``messages``. Optional: ``options``,
          ``result_delivery`` (``{bus_thread, bus_from_agent, bus_to_agent,
          bus_subject[, bus_brief_summary, bus_lifecycle]}`` — posts a
          pointer envelope at completion; receive then call ``op="result"``).
          Direct role-less model one-shots are FIRST-CLASS here:
          ``pipeline_id="chat-dispatch"`` (any frontier chat model via its
          native endpoint; ``options.model`` required; text / tool /
          structured output — not image/audio/video generation). For
          ROLE-based consults prefer ``team_dispatch`` (role contracts,
          default_model resolution, briefing assembly; the handler returns
          a redirect hint when a role is passed to ``chat-dispatch`` raw).

        - ``"result"`` — fetch or short-block on async-dispatched pipeline
          result. Returns tracker shape: ``{execution_id, pipeline, status,
          started_at, completed_at, result, error}``. Required:
          ``execution_id``. Optional: ``wait_seconds`` (server-side short-poll
          window; 0 = immediate; clamped to 60s at Stargate).

        - ``"validate"`` — validate pipeline YAML + model availability
          without consuming inference compute. Returns ``{valid, pipeline,
          steps, models, domain, errors}``. Required: ``pipeline_id``.

        - ``"stats"`` — tracker occupancy snapshot:
          ``{running, completed, failed, terminal, max_records,
          retention_seconds, oldest_terminal_age_seconds,
          oldest_running_age_seconds}``. No required params.

        - ``"cancel"`` — cancel an in-flight async-dispatched execution.
          Returns the terminal tracker record. Required: ``execution_id``.
          Idempotent: cancelling a completed execution returns it unchanged.

        Typical iteration flow: edit → ``op="validate"`` →
        ``quality_gate(files=[...])`` → ``op="run"`` or ``op="async"`` →
        ``observability(operation="pipeline-trace", params={"execution_id": "..."})``.
        """
        if op == "run":
            if not pipeline_id or messages is None:
                return {
                    "error": {
                        "code": "missing_required",
                        "message": "op=run requires pipeline_id and messages",
                    }
                }
            return _pipeline_run(pipeline_id, messages, options, timeout)
        if op == "async":
            if not pipeline_id or messages is None:
                return {
                    "error": {
                        "code": "missing_required",
                        "message": "op=async requires pipeline_id and messages",
                    }
                }
            return _pipeline_async(
                pipeline_id,
                messages,
                options,
                result_delivery,
                caller_agent,
            )
        if op == "result":
            if not execution_id:
                return {
                    "error": {
                        "code": "missing_required",
                        "message": "op=result requires execution_id",
                    }
                }
            return _pipeline_result(execution_id, wait_seconds)
        if op == "validate":
            if not pipeline_id:
                return {
                    "error": {
                        "code": "missing_required",
                        "message": "op=validate requires pipeline_id",
                    }
                }
            return _pipeline_validate(pipeline_id)
        if op == "stats":
            return _pipeline_stats()
        if op == "cancel":
            if not execution_id:
                return {
                    "error": {
                        "code": "missing_required",
                        "message": "op=cancel requires execution_id",
                    }
                }
            return _pipeline_cancel(execution_id)
        return {
            "error": {
                "code": "unknown_op",
                "message": (
                    f"Unknown op: {op}. Valid: run|async|result|validate|stats|cancel"
                ),
            }
        }

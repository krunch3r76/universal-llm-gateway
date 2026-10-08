"""Pipeline tool — unified ``pipeline(op, …)`` primary MCP surface.

Single primary tool dispatching by
``op ∈ {run, async, result, validate, stats, cancel, list}``
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


def _stargate_headers() -> dict[str, str]:
    """Copy the bound request surface onto Stargate calls.

    Absent metadata sends no surface header. The jobs satellite then refuses
    the call; this helper does not invent a surface.
    """
    from request_profile import current_request_metadata

    surface = (current_request_metadata() or {}).get("surface")
    if isinstance(surface, str) and surface:
        return {"X-ULG-Surface": surface}
    return {}


STARGATE_URL = os.environ.get("STARGATE_URL", "http://io:9999")
_RUN_TIMEOUT_FALLBACK = 480.0
_TIMEOUT_BUFFER = 30.0
_VALIDATE_TIMEOUT = 15.0
_DISPATCH_TIMEOUT = 15.0
_RESULT_MAX_WAIT = 60.0
_RESULT_POLL_BUFFER = 15.0
_CAPABILITY_PREFIX = "/api/v1/capabilities/"

_QUERY_SOCKET = os.environ.get(
    "EVENTS_QUERY_SOCK", "/tmp/universal-protocol/events-query.sock"
)

_pipeline_timeouts: dict[str, float] = {}
_last_timeout_refresh_monotonic = 0.0
_TIMEOUT_CACHE_TTL_S = 60.0


def _fetch_pipelines_metadata() -> dict[str, Any]:
    """Fetch pipeline registry metadata from Stargate.

    Returns:
        Decoded JSON payload from ``/api/v1/capabilities``.
    """
    url = "/api/v1/capabilities"
    try:
        with make_sync_client(STARGATE_URL, timeout=_VALIDATE_TIMEOUT) as client:
            resp = client.get(url, headers=_stargate_headers())
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

    pipelines = data.get("members", data.get("pipelines", {}))
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
    """Call the origin observability resource described by a legacy body."""
    from event_store.query_client import list_members, query_member, query_sql

    url = f"unix://{_QUERY_SOCKET}"
    try:
        kind = body.get("type")
        if kind == "sql":
            return query_sql(
                str(body.get("sql") or ""),
                params=body.get("params") or None,
                limit=int(body.get("limit", 100)),
                url=url,
            )
        if kind == "operations":
            return list_members(url=url)
        return query_member(
            str(body.get("name") or ""),
            body.get("params") or {},
            url=url,
        )
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
            resp = client.post(url, json=body, headers=_stargate_headers())
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

    try:
        with make_sync_client(STARGATE_URL, timeout=_DISPATCH_TIMEOUT) as client:
            listed = client.get(
                "/api/v1/capabilities",
                params={"id": pipeline_id},
                headers=_stargate_headers(),
            )
            if listed.status_code >= 400:
                try:
                    return listed.json()
                except ValueError:
                    return {
                        "error": {
                            "code": f"http_{listed.status_code}",
                            "message": listed.text[:500],
                        }
                    }
            url = listed.json()["url"]
            resp = client.post(url, json=body, headers=_stargate_headers())
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


def _capability_async(
    member_path: str,
    options: dict[str, Any] | None,
    messages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """POST a ``<category>/<member>`` run through the Stargate capability relay.

    Stargate swaps in the satellite token (capabilities.yaml ``auth_env``); this
    call carries only the bound surface. Error bodies pass through with
    ``status_code`` so an unknown category or member is not a local error.
    Local pipeline members take ``options.model`` (or flat options/messages).
    The bare id (e.g. ``writer-specialist-v1``) is an alias.
    """
    opts = options or {}
    flat = any(
        key not in {"args", "output_contract", "model", "pipeline_options"}
        for key in opts
    )
    model = opts.get("model")
    local = (
        (isinstance(model, str) and bool(model.strip()))
        or isinstance(opts.get("pipeline_options"), dict)
        or (isinstance(messages, list) and len(messages) > 0)
        or flat
    )
    if local:
        member = member_path.split("/")[-1]
        if isinstance(opts.get("pipeline_options"), dict):
            pipeline_options = opts["pipeline_options"]
        else:
            pipeline_options = {
                key: value
                for key, value in opts.items()
                if key not in {"args", "output_contract", "model", "pipeline_options"}
            }
        body: dict[str, Any] = {
            "model": opts.get("model") or member,
            "messages": messages or [],
            "pipeline_options": pipeline_options,
            "output_contract": opts.get("output_contract", "inline"),
        }
    else:
        body = {
            "args": opts.get("args") or {},
            "output_contract": opts.get("output_contract", "inline"),
        }
    record("mcp.pipeline.async.called", pipeline=member_path)
    try:
        with make_sync_client(STARGATE_URL, timeout=_DISPATCH_TIMEOUT) as client:
            resp = client.post(
                f"{_CAPABILITY_PREFIX}{member_path}",
                json=body,
                headers=_stargate_headers(),
            )
    except httpx.ConnectError as exc:
        record("mcp.pipeline.async.failed", pipeline=member_path, error="connect_error")
        return annotate_unreachable_error(
            code="stargate_unreachable",
            message=f"Stargate not reachable: {exc}",
            service="stargate",
        )
    except httpx.HTTPError as exc:
        record("mcp.pipeline.async.failed", pipeline=member_path, error=str(exc))
        return {"error": {"code": "http_error", "message": str(exc)}}
    try:
        payload = resp.json()
    except ValueError:
        payload = {
            "error": {"code": f"http_{resp.status_code}", "message": resp.text[:500]}
        }
    if not isinstance(payload, dict):
        payload = {"result": payload}
    if resp.status_code >= 400:
        record(
            "mcp.pipeline.async.failed",
            pipeline=member_path,
            status_code=resp.status_code,
        )
        payload.setdefault("status_code", resp.status_code)
        return payload
    location = resp.headers.get("location")
    if location:
        payload.setdefault("href", location)
    record(
        "mcp.pipeline.async.dispatched",
        pipeline=member_path,
        execution_id=str(payload.get("run_id", "")),
    )
    return payload


def _pipeline_stats() -> dict[str, Any]:
    """Fetch tracker occupancy snapshot from Stargate."""
    url = "/api/v1/executions/stats"
    try:
        with make_sync_client(STARGATE_URL, timeout=_VALIDATE_TIMEOUT) as client:
            resp = client.get(url, headers=_stargate_headers())
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as exc:
        return {"error": {"code": "stargate_http_error", "message": str(exc)}}


def _pipeline_cancel(execution_id: str) -> dict[str, Any]:
    """Cancel an in-flight execution — GIW SDK dispatch first, else Stargate."""
    giw_body = _giw_cancel_dispatch(execution_id)
    if giw_body is not None:
        return giw_body
    url = f"/api/v1/executions/{execution_id}"
    try:
        with make_sync_client(STARGATE_URL, timeout=_DISPATCH_TIMEOUT) as client:
            resp = client.delete(url, headers=_stargate_headers())
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as exc:
        return {"error": {"code": "stargate_http_error", "message": str(exc)}}


def _giw_cancel_dispatch(dispatch_id: str) -> dict[str, Any] | None:
    """Relay cancel to GIW when the id is an SDK ledger row."""
    from tools.agent_bus.worker_http import _worker_base_url

    url = f"{_worker_base_url()}/api/v1/cursor/dispatch/{dispatch_id}"
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        with httpx.Client(timeout=_DISPATCH_TIMEOUT) as client:
            resp = client.delete(url, headers=headers)
            if resp.status_code == 404:
                return None
            if resp.content:
                body = resp.json()
            else:
                body = {"ok": True}
            if resp.status_code >= 400:
                if isinstance(body, dict):
                    body.setdefault("status_code", resp.status_code)
                return body
            return body
    except httpx.RequestError:
        return None


def _pipeline_result(execution_id: str, wait_seconds: float) -> dict[str, Any]:
    record("mcp.pipeline.result.called", execution_id=execution_id)

    wait_clamped = max(0.0, min(wait_seconds, _RESULT_MAX_WAIT))
    http_timeout = wait_clamped + _RESULT_POLL_BUFFER

    url = f"/api/v1/executions/{execution_id}"
    wait_param: float = wait_clamped
    if execution_id.startswith(_CAPABILITY_PREFIX):
        url = execution_id
        wait_param = int(wait_clamped)
    try:
        with make_sync_client(STARGATE_URL, timeout=http_timeout) as client:
            resp = client.get(
                url, params={"wait": wait_param}, headers=_stargate_headers()
            )
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


def _pipeline_list(category: str | None = None) -> dict[str, Any]:
    """Relay ``GET /api/v1/capabilities`` with an optional category filter."""
    params = {"category": category} if category else None
    try:
        with make_sync_client(STARGATE_URL, timeout=_VALIDATE_TIMEOUT) as client:
            resp = client.get(
                "/api/v1/capabilities", params=params, headers=_stargate_headers()
            )
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
    except httpx.HTTPError as exc:
        return {"error": {"code": "stargate_http_error", "message": str(exc)}}


def _pipeline_validate(pipeline_id: str) -> dict[str, Any]:
    t0 = monotonic_now()
    record("mcp.pipeline.validate.called", pipeline=pipeline_id)

    try:
        with make_sync_client(STARGATE_URL, timeout=_VALIDATE_TIMEOUT) as client:
            resp = client.get(
                "/api/v1/capabilities",
                params={"id": pipeline_id},
                headers=_stargate_headers(),
            )
    except httpx.ConnectError as e:
        return _validate_error(pipeline_id, f"Stargate not reachable: {e}")
    except httpx.HTTPError as e:
        return _validate_error(pipeline_id, f"Validation failed: {e}")

    if resp.status_code == 404:
        try:
            payload = resp.json()
        except ValueError:
            payload = {
                "error": {
                    "code": "capability_not_found",
                    "message": resp.text[:500],
                    "data": {"near_matches": []},
                }
            }
        return payload if isinstance(payload, dict) else {"error": payload}
    if resp.status_code >= 400:
        return _validate_error(pipeline_id, f"Pipeline API error: {resp.status_code}")

    info = resp.json()
    if not isinstance(info, dict):
        return _validate_error(
            pipeline_id, "Pipeline API returned invalid metadata payload."
        )
    _cache_pipeline_timeouts({pipeline_id: info})
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
        op: Literal["run", "async", "result", "validate", "stats", "cancel", "list"],
        pipeline_id: str | None = None,
        messages: list[dict[str, str]] | None = None,
        execution_id: str | None = None,
        options: dict[str, Any] | None = None,
        timeout: float | None = None,
        result_delivery: dict[str, Any] | None = None,
        caller_agent: str | None = None,
        wait_seconds: float = 0.0,
        category: str | None = None,
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
          A ``pipeline_id`` of the form ``<category>/<member>`` for a satellite
          category (e.g. ``jobs/bus-reply-watch``) POSTs ``{args, output_contract}``
          taken from ``options`` to ``/api/v1/capabilities/{category}/{member}``.
          ``messages`` is not required. The result is the relay body plus
          ``href`` (the run href). Error bodies carry ``status_code``.

        - ``"result"`` — fetch or short-block on async-dispatched pipeline
          result. Returns tracker shape: ``{execution_id, pipeline, status,
          started_at, completed_at, result, error}``. Required:
          ``execution_id``. Optional: ``wait_seconds`` (server-side short-poll
          window; 0 = immediate; clamped to 60s at Stargate).
          ``execution_id`` may also be a ``/api/v1/capabilities/...`` run href
          returned by a ``<category>/<member>`` async call.

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
            if pipeline_id and "/" in pipeline_id:
                return _capability_async(pipeline_id, options, messages)
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
        if op == "list":
            return _pipeline_list(category)
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

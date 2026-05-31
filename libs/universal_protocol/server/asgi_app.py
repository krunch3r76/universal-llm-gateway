"""Starlette ASGI application with JSON-RPC and WebSocket streaming routes.

Implements the two main endpoints:
- POST /rpc - JSON-RPC 2.0 request handler
- WS /stream/{stream_id} - WebSocket streaming (SSE format)

This is a minimal MVP implementation that routes requests to handlers.
Actual business logic (model loading, inference) is outside this module.
"""

import json
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route, WebSocketRoute
from universal_logging import get_logger

from universal_protocol.errors import ProtocolError, RPCError
from universal_protocol.observability import increment_rpc_error, increment_rpc_request
from universal_protocol.rpc.handlers import (
    handle_cancel_inference,
    handle_count_tokens,
    handle_debug_stats,
    handle_health,
    handle_load_model,
    handle_unload_model,
)
from universal_protocol.ws import stream_handler

logger = get_logger(__name__)


# ============================================================================
# RPC Handler (POST /rpc)
# ============================================================================


async def rpc_handler(request: Request) -> JSONResponse:
    """Handle JSON-RPC 2.0 requests.

    Routes incoming RPC requests to appropriate method handlers.
    Validates request format and returns JSON-RPC response envelope.

    Args:
        request: Starlette request object

    Returns:
        JSONResponse with JSON-RPC 2.0 response envelope

    Response format:
        {
            "jsonrpc": "2.0",
            "result": {...} or "error": {...},
            "id": "request-id"
        }

    Error responses include:
        {
            "jsonrpc": "2.0",
            "error": {
                "code": -32600,  # JSON-RPC error codes
                "message": "Invalid Request",
                "data": {
                    "code": "INVALID_REQUEST",  # Protocol error code
                    "source": "rpc",
                    "detail": "..."
                }
            },
            "id": "request-id"
        }
    """
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError) as e:
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "error": {
                    "code": -32700,
                    "message": "Parse error",
                    "data": {
                        "code": "PARSE_ERROR",
                        "source": "rpc",
                        "message": str(e),
                    },
                },
                "id": None,
            },
            status_code=400,
        )

    # Validate basic JSON-RPC structure
    if not isinstance(body, dict):
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "error": {
                    "code": -32600,
                    "message": "Invalid Request",
                    "data": {
                        "code": "INVALID_REQUEST",
                        "source": "rpc",
                        "message": "Request must be a JSON object",
                    },
                },
                "id": None,
            },
            status_code=400,
        )

    jsonrpc_version = body.get("jsonrpc")
    method = body.get("method")
    params = body.get("params", {})
    request_id = body.get("id")

    # Validate correlation_id presence (warning only for backward compatibility)
    correlation_id = params.get("correlation_id") if params else None
    if not correlation_id:
        logger.warning(
            f"[request_id={request_id}] RPC request missing correlation_id "
            f"for method: {method}"
        )

    # Log incoming RPC request with both request_id and correlation_id
    log_prefix = f"[request_id={request_id}]"
    if correlation_id:
        log_prefix = f"[request_id={request_id}][correlation_id={correlation_id}]"

    logger.info(f"{log_prefix} RPC request: {method}")
    if params:
        pass
        # logger.debug(f"{log_prefix} RPC params: {params}")

    # Check JSON-RPC 2.0 version
    if jsonrpc_version != "2.0":
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "error": {
                    "code": -32600,
                    "message": "Invalid Request",
                    "data": {
                        "code": "INVALID_VERSION",
                        "source": "rpc",
                        "message": f"Expected jsonrpc=2.0, got {jsonrpc_version}",
                    },
                },
                "id": request_id,
            },
            status_code=400,
        )

    # Check method presence
    if not method or not isinstance(method, str):
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "error": {
                    "code": -32600,
                    "message": "Invalid Request",
                    "data": {
                        "code": "MISSING_METHOD",
                        "source": "rpc",
                        "message": "method field required (string)",
                    },
                },
                "id": request_id,
            },
            status_code=400,
        )

    # Route to method handler
    handler = RPC_METHODS.get(method)
    if not handler:
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "error": {
                    "code": -32601,
                    "message": "Method not found",
                    "data": {
                        "code": "METHOD_NOT_FOUND",
                        "source": "rpc",
                        "method": method,
                    },
                },
                "id": request_id,
            },
            status_code=404,
        )

    # Call method handler
    try:
        # Track RPC request
        increment_rpc_request(method)

        # Add request_id to params for tracing
        params_with_id = params.copy() if params else {}
        params_with_id["_request_id"] = request_id

        result = await handler(params_with_id)

        logger.info(f"[request_id={request_id}] RPC success: {method}")
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "result": result,
                "id": request_id,
            }
        )
    except RPCError as e:
        error_data = e.to_dict()
        logger.error(
            f"[request_id={request_id}] RPC error: {method} failed with {error_data}"
        )

        # Track RPC error with source
        increment_rpc_error(
            error_data.get("code", "UNKNOWN"), error_data.get("source", "rpc")
        )

        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "error": {
                    "code": -32603,
                    "message": "Internal error",
                    "data": error_data,
                },
                "id": request_id,
            },
            status_code=500,
        )
    except ProtocolError as e:
        # Handle EngineError and StreamError (subclasses of ProtocolError)
        error_data = e.to_dict()
        logger.error(
            f"[request_id={request_id}] Protocol error: {method} "
            f"failed with {error_data}"
        )

        # Track error by code and source
        increment_rpc_error(
            error_data.get("code", "UNKNOWN"), error_data.get("source", "rpc")
        )

        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "error": {
                    "code": -32603,
                    "message": "Internal error",
                    "data": error_data,
                },
                "id": request_id,
            },
            status_code=500,
        )
    except Exception as e:
        logger.exception(
            f"[request_id={request_id}] Unexpected error in RPC handler {method}: {e}"
        )
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "error": {
                    "code": -32603,
                    "message": "Internal error",
                    "data": {
                        "code": "INTERNAL_ERROR",
                        "source": "rpc",
                        "message": str(e),
                    },
                },
                "id": request_id,
            },
            status_code=500,
        )


# ============================================================================
# Bridge Handlers for Process IPC Compatibility
# ============================================================================
#
# Note: These are placeholder handlers that provide default implementations.
# Workers should override these by registering their own handlers in RPC_METHODS
# during initialization via _configure_rpc_handlers().
#
# If a worker-specific handler is not registered, these fallback handlers will
# execute, which typically means the command won't do anything meaningful.
# ============================================================================


async def handle_health_check(params: dict[str, Any]) -> dict[str, Any]:
    """Fallback health check handler - delegates to standard health handler.

    Workers should override this by registering their own health handler.

    Args:
        params: Request parameters (typically contains worker_id)

    Returns:
        Health check response dict
    """
    return await handle_health(params)


async def handle_process_command(params: dict[str, Any]) -> dict[str, Any]:
    """Fallback process_command handler.

    Workers MUST override this by registering their own process_command handler
    in RPC_METHODS during _configure_rpc_handlers(). This fallback will raise
    an error to indicate that the worker didn't register a handler.

    Args:
        params: Command payload (contains command_type, payload, etc.)

    Returns:
        Command execution result

    Raises:
        NotImplementedError: If worker didn't register its own handler
    """
    command_type = params.get("command_type", "unknown")
    logger.error(
        f"[fallback] process_command called but no worker handler registered. "
        f"Command: {command_type}. Worker must register process_command handler."
    )
    raise NotImplementedError(
        "process_command not implemented by worker. "
        "Worker must register a process_command handler in RPC_METHODS."
    )


async def handle_process_data(params: dict[str, Any]) -> dict[str, Any]:
    """Fallback process_data handler.

    Workers should override this by registering their own data handler
    if they need to process data sent via send_data().

    Args:
        params: Data payload (contains worker_id, data, etc.)

    Returns:
        Processing acknowledgment

    Raises:
        NotImplementedError: If worker didn't register its own handler
    """
    worker_id = params.get("worker_id", "unknown")
    logger.error(
        f"[fallback] process_data called but no worker handler registered "
        f"for {worker_id}. Worker must register process_data handler "
        f"if using send_data()."
    )
    raise NotImplementedError(
        "process_data not implemented by worker. "
        "Worker must register a process_data handler in RPC_METHODS."
    )


async def handle_start_stream(params: dict[str, Any]) -> dict[str, Any]:
    """Fallback start_stream handler - requires worker implementation.

    Workers MUST override this by registering their own handler that implements:
    - Concurrent stream limit checking
    - Cancellation event creation
    - Background inference task creation
    - ACTIVE_STREAMS registration

    Raises:
        NotImplementedError: Always - workers must provide implementation
    """
    raise NotImplementedError(
        "start_stream/start_inference not implemented. "
        "Worker must register handler via RPC_METHODS.update()"
    )


async def handle_list_models(params: dict[str, Any]) -> dict[str, Any]:
    """Handle list_models RPC request.

    Returns the list of loaded models (extracted from health status).
    This provides JSON-RPC compatibility for generic clients that call
    client.call('list_models', {}) instead of client.list_models().

    Args:
        params: Request parameters (none expected)

    Returns:
        Dict with:
            - models: List of loaded model names
            - status: Health status ("ready", "busy", "error")
    """
    health_info = await handle_health(params)
    return {
        "models": health_info.get("models", []),
        "status": health_info.get("status", "unknown"),
    }


# ============================================================================
# Supervisor Handlers for ProcessSupervisor HTTP RPC
# ============================================================================


async def handle_supervisor_health_check(params: dict[str, Any]) -> dict[str, Any]:
    """Handle supervisor health check request.

    Returns process-level health information for the supervisor protocol.
    """
    # Delegate to standard health handler but add supervisor-specific fields
    health_info = await handle_health(params)

    # Add supervisor protocol fields
    return {
        "status": health_info.get("status", "unknown"),
        "models": health_info.get("models", []),
        "worker_id": params.get("worker_id"),
        "uptime": (
            time.time() - app.state.start_time
            if hasattr(app.state, "start_time")
            else 0
        ),
        "memory_usage": health_info.get("memory_usage", {}),
        "supervisor_ready": True,
    }


async def handle_supervisor_process_command(params: dict[str, Any]) -> dict[str, Any]:
    """Handle supervisor process_command request.

    This is the main command dispatch for supervisor operations.
    Workers must override this by registering their own handler.
    """
    # This default handler should be replaced by worker's handler via
    # SUPERVISOR_RPC_METHODS.update()
    # If we reach here, it means the worker hasn't registered its handler yet
    raise NotImplementedError(
        "Worker must register process_command handler for supervisor operations"
    )


async def handle_supervisor_process_data(params: dict[str, Any]) -> dict[str, Any]:
    """Handle supervisor process_data request."""
    # Check if worker registered a custom handler
    if "process_data" in RPC_METHODS:
        return await RPC_METHODS["process_data"](params)

    # Fallback - just acknowledge
    return {"status": "acknowledged", "worker_id": params.get("worker_id")}


# Supervisor RPC method dispatch
SUPERVISOR_RPC_METHODS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "health_check": handle_supervisor_health_check,
    "process_command": handle_supervisor_process_command,
    "process_data": handle_supervisor_process_data,
}


async def supervisor_rpc_handler(request: Request) -> JSONResponse:
    """Handle supervisor JSON-RPC 2.0 requests.

    This is a separate RPC endpoint for ProcessSupervisor operations,
    keeping supervisor and inference protocols cleanly separated.
    """
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError) as e:
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "error": {
                    "code": -32700,
                    "message": "Parse error",
                    "data": {
                        "code": "PARSE_ERROR",
                        "source": "supervisor",
                        "message": str(e),
                    },
                },
                "id": None,
            },
            status_code=400,
        )

    # Extract request fields
    jsonrpc_version = body.get("jsonrpc")
    method = body.get("method")
    params = body.get("params", {})
    request_id = body.get("id")

    # Log supervisor RPC request
    logger.info(f"[supervisor] RPC request: {method} (id={request_id})")

    # Validate JSON-RPC version
    if jsonrpc_version != "2.0":
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "error": {
                    "code": -32600,
                    "message": "Invalid Request",
                    "data": {
                        "code": "INVALID_VERSION",
                        "source": "supervisor",
                        "message": f"Expected jsonrpc=2.0, got {jsonrpc_version}",
                    },
                },
                "id": request_id,
            },
            status_code=400,
        )

    # Route to supervisor method handler
    handler = SUPERVISOR_RPC_METHODS.get(method)
    if not handler:
        # Check if it's a standard RPC method that supervisor can use
        if method in RPC_METHODS:
            handler = RPC_METHODS[method]
        else:
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "error": {
                        "code": -32601,
                        "message": "Method not found",
                        "data": {
                            "code": "METHOD_NOT_FOUND",
                            "source": "supervisor",
                            "method": method,
                        },
                    },
                    "id": request_id,
                },
                status_code=404,
            )

    # Call method handler
    try:
        result = await handler(params)

        logger.info(f"[supervisor] RPC success: {method}")
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "result": result,
                "id": request_id,
            }
        )
    except Exception as e:
        logger.exception(f"[supervisor] RPC error in {method}: {e}")

        # Map exceptions to appropriate error responses
        error_code = "INTERNAL_ERROR"
        status_code = 500

        if isinstance(e, NotImplementedError):
            error_code = "NOT_IMPLEMENTED"
            status_code = 501
        elif isinstance(e, TimeoutError):
            error_code = "TIMEOUT"
            status_code = 504
        elif isinstance(e, ValueError):
            error_code = "INVALID_PARAMS"
            status_code = 400

        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "error": {
                    "code": -32603,
                    "message": "Internal error",
                    "data": {
                        "code": error_code,
                        "source": "supervisor",
                        "message": str(e),
                    },
                },
                "id": request_id,
            },
            status_code=status_code,
        )


# ============================================================================
# Method Dispatch Table
# ============================================================================

RPC_METHODS: dict[str, Callable[[dict[str, Any]], Any]] = {
    # Standard inference methods
    "load_model": handle_load_model,
    "unload_model": handle_unload_model,
    "health": handle_health,
    "list_models": handle_list_models,
    "count_tokens": handle_count_tokens,
    "cancel_inference": handle_cancel_inference,
    "debug_stats": handle_debug_stats,
    # Fallback bridge methods for process_ipc compatibility
    # Workers MUST override start_inference/start_stream with their own handler
    "health_check": handle_health_check,
    "process_command": handle_process_command,
    "process_data": handle_process_data,
    "start_stream": handle_start_stream,
}


# ============================================================================
# Metrics Handler
# ============================================================================


async def metrics_handler(request: Request) -> Response:
    """Handle metrics endpoint requests.

    Supports both JSON (default) and Prometheus text format based on 'format' query parameter.

    Args:
        request: Starlette request with optional format=prometheus query parameter

    Returns:
        Response with metrics in requested format
    """
    from universal_protocol.observability import get_metrics

    # Check requested format (default to json for backward compatibility)
    format_param = request.query_params.get("format", "json")

    metrics = get_metrics()

    # Format metrics for output - comprehensive JSON including all backpressure counters
    formatted_metrics = {
        "timestamp": time.time(),
        "rpc": {
            "requests": dict(metrics.get("rpc_requests_total", {})),
            "errors": dict(metrics.get("rpc_errors_by_code", {})),
            "errors_by_source": dict(metrics.get("rpc_errors_by_source", {})),
            "latency": metrics.get("rpc_latency_stats", {}),
        },
        "streams": {
            "active": metrics.get("streams_active", 0),
            "total_started": metrics.get("streams_total", 0),
            "duration_stats": metrics.get("stream_duration_stats", {}),
        },
        "backpressure": {
            "queue_timeouts": metrics.get("queue_timeouts_total", 0),
            "events": metrics.get("backpressure_events", 0),
            "queue_depth_by_stream": dict(metrics.get("queue_depth_by_stream", {})),
            "queue_depth_total": metrics.get("queue_depth_total", 0),
        },
        "throughput": {
            "token_throughput_stats": metrics.get("token_throughput_stats", {}),
        },
        "summary": {
            "queue_full": metrics.get("queue_timeouts_total", 0),
            "timeouts": metrics.get("queue_timeouts_total", 0),
            "backpressure_events": metrics.get("backpressure_events", 0),
            "streams_active": metrics.get("streams_active", 0),
            "total_errors": sum(metrics.get("rpc_errors_by_code", {}).values()),
        },
    }

    # Return in requested format
    if format_param == "prometheus":
        # Convert to Prometheus text format
        prometheus_lines = []

        # RPC request metrics
        prometheus_lines.append(
            "# HELP universal_protocol_rpc_requests_total Total RPC requests by method"
        )
        prometheus_lines.append("# TYPE universal_protocol_rpc_requests_total counter")
        for method, count in metrics.get("rpc_requests_total", {}).items():
            prometheus_lines.append(
                f'universal_protocol_rpc_requests_total{{method="{method}"}} {count}'
            )

        # RPC error metrics by code and source
        prometheus_lines.append(
            "# HELP universal_protocol_rpc_errors_total Total RPC errors by code and"
            "source"
        )
        prometheus_lines.append("# TYPE universal_protocol_rpc_errors_total counter")
        for source, codes in metrics.get("rpc_errors_by_source", {}).items():
            for code, count in codes.items():
                prometheus_lines.append(
                    f'universal_protocol_rpc_errors_total{{code="{code}",'
                    f'source="{source}"}} {count}'
                )

        # Active streams gauge
        prometheus_lines.append(
            "# HELP universal_protocol_streams_active Current number of active streams"
        )
        prometheus_lines.append("# TYPE universal_protocol_streams_active gauge")
        prometheus_lines.append(
            f"universal_protocol_streams_active {metrics.get('streams_active', 0)}"
        )

        # Queue timeouts counter (total and by model)
        prometheus_lines.append(
            "# HELP universal_protocol_queue_timeouts_total Total queue timeout events"
        )
        prometheus_lines.append(
            "# TYPE universal_protocol_queue_timeouts_total counter"
        )
        prometheus_lines.append(
            f"universal_protocol_queue_timeouts_total "
            f"{metrics.get('queue_timeouts_total', 0)}"
        )

        # Queue timeouts by model
        for model, count in metrics.get("queue_timeouts_by_model", {}).items():
            prometheus_lines.append(
                f'universal_protocol_queue_timeouts_total{{model="{model}"}} {count}'
            )

        # Backpressure events counter (total and by model)
        prometheus_lines.append(
            "# HELP universal_protocol_backpressure_events_total Total backpressure"
            "events"
        )
        prometheus_lines.append(
            "# TYPE universal_protocol_backpressure_events_total counter"
        )
        prometheus_lines.append(
            f"universal_protocol_backpressure_events_total "
            f"{metrics.get('backpressure_events', 0)}"
        )

        # Backpressure events by model
        for model, count in metrics.get("backpressure_events_by_model", {}).items():
            prometheus_lines.append(
                f'universal_protocol_backpressure_events_total{{model="{model}"}} '
                f"{count}"
            )

        # Queue depth gauge (total)
        prometheus_lines.append(
            "# HELP universal_protocol_queue_depth_total Total items in all queues"
        )
        prometheus_lines.append("# TYPE universal_protocol_queue_depth_total gauge")
        prometheus_lines.append(
            f"universal_protocol_queue_depth_total {metrics.get('queue_depth_total', 0)}"
        )

        # RPC latency statistics (if available)
        if "rpc_latency_stats" in metrics:
            prometheus_lines.append(
                "# HELP universal_protocol_rpc_latency_seconds RPC latency statistics"
            )
            prometheus_lines.append(
                "# TYPE universal_protocol_rpc_latency_seconds summary"
            )
            for method, stats in metrics["rpc_latency_stats"].items():
                if "avg_seconds" in stats:
                    prometheus_lines.append(
                        f'universal_protocol_rpc_latency_seconds{{method="{method}",'
                        f'quantile="0.5"}} {stats["avg_seconds"]}'
                    )
                if "min_seconds" in stats:
                    prometheus_lines.append(
                        f'universal_protocol_rpc_latency_seconds{{method="{method}",'
                        f'quantile="0.0"}} {stats["min_seconds"]}'
                    )
                if "max_seconds" in stats:
                    prometheus_lines.append(
                        f'universal_protocol_rpc_latency_seconds{{method="{method}",'
                        f'quantile="1.0"}} {stats["max_seconds"]}'
                    )
                if "count" in stats:
                    prometheus_lines.append(
                        f'universal_protocol_rpc_latency_seconds_count{{method="{method}"}} '
                        f"{stats['count']}"
                    )

        # Stream duration histogram (if available)
        if (
            "stream_duration_histogram" in metrics
            and metrics["stream_duration_histogram"]
        ):
            prometheus_lines.append(
                "# HELP universal_protocol_stream_duration_seconds_bucket Stream"
                "duration histogram buckets"
            )
            prometheus_lines.append(
                "# TYPE universal_protocol_stream_duration_seconds_bucket histogram"
            )

            # Output histogram buckets in Prometheus format
            for bucket_label, cumulative_count in sorted(
                metrics["stream_duration_histogram"].items(),
                key=lambda x: float("inf") if x[0] == "+Inf" else float(x[0]),
            ):
                prometheus_lines.append(
                    f'universal_protocol_stream_duration_seconds_bucket{{le="{bucket_label}"}} '
                    f"{cumulative_count}"
                )

            # Add total count
            if "stream_duration_histogram_count" in metrics:
                prometheus_lines.append(
                    f"universal_protocol_stream_duration_seconds_count "
                    f"{metrics['stream_duration_histogram_count']}"
                )

            # Add sum if we have duration stats
            if "stream_duration_stats" in metrics and metrics["stream_duration_stats"]:
                stats = metrics["stream_duration_stats"]
                if "avg_seconds" in stats and "count" in stats:
                    total_sum = stats["avg_seconds"] * stats["count"]
                    prometheus_lines.append(
                        f"universal_protocol_stream_duration_seconds_sum {total_sum}"
                    )

        # Stream duration statistics (legacy summary format - kept for compatibility)
        elif "stream_duration_stats" in metrics and metrics["stream_duration_stats"]:
            stats = metrics["stream_duration_stats"]
            prometheus_lines.append(
                "# HELP universal_protocol_stream_duration_seconds Stream duration"
                "statistics"
            )
            prometheus_lines.append(
                "# TYPE universal_protocol_stream_duration_seconds summary"
            )
            if "avg_seconds" in stats:
                prometheus_lines.append(
                    f'universal_protocol_stream_duration_seconds{{quantile="0.5"}} '
                    f"{stats['avg_seconds']}"
                )
            if "min_seconds" in stats:
                prometheus_lines.append(
                    f'universal_protocol_stream_duration_seconds{{quantile="0.0"}} '
                    f"{stats['min_seconds']}"
                )
            if "max_seconds" in stats:
                prometheus_lines.append(
                    f'universal_protocol_stream_duration_seconds{{quantile="1.0"}} '
                    f"{stats['max_seconds']}"
                )
            if "count" in stats:
                prometheus_lines.append(
                    f"universal_protocol_stream_duration_seconds_count {stats['count']}"
                )

        # Token throughput statistics (if available)
        if "token_throughput_stats" in metrics and metrics["token_throughput_stats"]:
            stats = metrics["token_throughput_stats"]
            prometheus_lines.append(
                "# HELP universal_protocol_token_throughput_per_second Token generation"
                "throughput"
            )
            prometheus_lines.append(
                "# TYPE universal_protocol_token_throughput_per_second gauge"
            )
            if "avg_tokens_per_second" in stats:
                prometheus_lines.append(
                    f"universal_protocol_token_throughput_per_second {stats['avg_tokens_per_second']}"
                )

        # Join lines and return as plain text
        prometheus_text = "\n".join(prometheus_lines) + "\n"
        return Response(content=prometheus_text, media_type="text/plain; version=0.0.4")
    else:
        # Default to JSON format
        return JSONResponse(formatted_metrics)


# ============================================================================
# Application Setup
# ============================================================================

routes = [
    Route("/rpc", rpc_handler, methods=["POST"]),
    Route("/supervisor/rpc", supervisor_rpc_handler, methods=["POST"]),  # NEW
    Route("/metrics", metrics_handler, methods=["GET"]),
    WebSocketRoute("/stream/{stream_id}", stream_handler),
]


@asynccontextmanager
async def lifespan(app: Starlette) -> AsyncIterator[None]:
    """Initialize and tear down minimal process-global app state."""
    app.state.start_time = time.time()
    logger.info("Universal Protocol ASGI app started (with supervisor support)")
    try:
        yield
    finally:
        logger.info("Universal Protocol ASGI app shutting down")


app = Starlette(routes=routes, lifespan=lifespan)

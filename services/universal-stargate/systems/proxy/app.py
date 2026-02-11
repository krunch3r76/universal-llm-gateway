"""Main FastAPI application for Universal Stargate Proxy"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from universal_logging import get_logger

from systems.federation.common.config import StargateMode, load_federation_config
from systems.federation.common.middleware import (
    EdgeFederationAuthMiddleware,
    EdgeModeEndpointGuard,
    FederationAuthMiddleware,
    HeaderSanitizationMiddleware,
    HopCountMiddleware,
    RemoteModeEndpointGuard,
)

from .core.common import ErrorNormalizer
from .core.streaming import StreamingErrorHandler
from .dependencies import get_proxy, init_proxy
from .middleware.raw_body_cache import RawBodyCacheMiddleware
from .routers import api, forwarding, health, monitoring, schedule, v1

logger = get_logger(__name__)


# ============================================================================
# Helper Functions for Error Handling
# ============================================================================


async def _check_if_streaming_request(request: Request) -> bool:
    """
    Check if the original request wanted streaming responses.

    This is critical for proper error handling - clients expecting streaming
    responses need errors in SSE format, not JSON format.

    Args:
        request: The FastAPI Request object

    Returns:
        True if client requested streaming, False otherwise
    """
    try:
        # Check if request body is already consumed
        if hasattr(request.state, "_json"):
            body = request.state._json
        else:
            # Try to get cached JSON body from request
            # This avoids re-reading the body which would fail
            body = await request.json()
            request.state._json = body  # Cache for future use

        return body.get("stream", False) if isinstance(body, dict) else False
    except Exception:
        # If we can't read the body (already consumed, invalid JSON, etc.),
        # assume non-streaming to be safe
        return False


async def _create_streaming_error_response(
    error_dict: dict, status_code: int
) -> StreamingResponse:
    """
    Create a streaming error response that properly closes the connection.

    When a client expects streaming but an error occurs before streaming starts,
    we need to:
    1. Return HTTP 200 (streaming has "started")
    2. Send error as SSE event
    3. Send [DONE] marker
    4. Close connection

    This ensures clients properly recognize the error and close the connection.

    Args:
        error_dict: OpenAI-formatted error dictionary
        status_code: HTTP status code (will be included in error event)

    Returns:
        StreamingResponse with SSE error event and [DONE] marker
    """

    async def error_stream():
        # Send error as SSE event
        yield StreamingErrorHandler.create_sse_error_event(error_dict)
        # Send [DONE] marker to signal end of stream
        yield StreamingErrorHandler.create_sse_done_event()

    # Return HTTP 200 with SSE content type so client recognizes streaming format
    # The actual error information is in the SSE event payload
    return StreamingResponse(
        error_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "close",  # Explicit connection closure
            "X-Error-Status": str(status_code),  # Include original status for debugging
        },
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown events"""
    import asyncio

    # Startup
    logger.info("🔍 Lifespan: Starting startup phase...")
    proxy = init_proxy(_federation_config)
    shutdown_reason = "unknown"

    # Task monitoring
    async def monitor_tasks():
        """Monitor background tasks and log if any complete unexpectedly."""
        await asyncio.sleep(10)  # Wait for startup to complete
        while True:
            all_tasks = [t for t in asyncio.all_tasks() if not t.done()]
            task_names = [t.get_name() for t in all_tasks]
            logger.debug(f"🔍 Active tasks: {len(all_tasks)}")

            # Check for completed tasks with exceptions
            done_tasks = [
                t for t in asyncio.all_tasks() if t.done() and not t.cancelled()
            ]
            for task in done_tasks:
                try:
                    exc = task.exception()
                    if exc:
                        logger.error(
                            f"🚨 TASK FAILED: {task.get_name()} - "
                            f"{type(exc).__name__}: {exc}",
                            exc_info=(type(exc), exc, exc.__traceback__),
                        )
                except Exception:
                    pass

            # Check for mode-specific critical tasks
            critical_tasks = ["HotReload-profiles"]  # Common to all modes

            # Add federation-specific tasks based on mode and config
            if _federation_config.mode == StargateMode.MASTER:
                # Master mode: HTTP telemetry polling ONLY if HTTP-based remotes exist
                # Pattern match for tasks like "http-telemetry-poller-golem-remote-1"
                has_http_remotes = any(
                    r.disable_websocket for r in _federation_config.remotes
                )
                if has_http_remotes:
                    critical_tasks.append("http-telemetry-poller")
            elif _federation_config.mode == StargateMode.EDGE:
                # Edge mode: Periodic telemetry heartbeat (prevents staleness)
                # Sent over bidirectional WebSocket from Edge → Relay/Master
                critical_tasks.append("federation-periodic-telemetry-heartbeat")
            elif _federation_config.mode == StargateMode.REMOTE:
                # Remote mode: Router-only, no Gateway, no periodic telemetry task
                # Relay forwards Edge telemetry to Master
                pass

            missing = [
                name
                for name in critical_tasks
                if not any(name in tn for tn in task_names)
            ]
            if missing:
                logger.error(f"🚨 CRITICAL TASKS MISSING: {missing}")
                logger.error("🚨 This may cause application shutdown!")

            await asyncio.sleep(15)  # Check every 15 seconds

    monitor_task = None

    try:
        await proxy.startup(app)
        logger.info(
            "🔍 Lifespan: Startup completed successfully, yielding to application..."
        )

        # Inject proxy into cancel API for queue + remote cancellation
        from .routers.api.v1.cancel import set_proxy

        set_proxy(proxy)
        logger.info("✅ Proxy injected into cancel API")

        # Start task monitor
        monitor_task = asyncio.create_task(monitor_tasks(), name="task-monitor")
        logger.info("🔍 Started background task monitor")

        yield  # Application runs here

        # Shutdown initiated (SIGTERM/SIGINT)
        shutdown_reason = "normal_shutdown"
        logger.info(
            f"🔍 Lifespan: Application is shutting down... (reason: {shutdown_reason})"
        )

    except asyncio.CancelledError:
        shutdown_reason = "cancelled"
        logger.info(f"🔍 Lifespan: CancelledError received (reason: {shutdown_reason})")
        raise
    except Exception as e:
        shutdown_reason = f"exception_{type(e).__name__}"
        logger.error(
            f"🔍 Lifespan: Exception: {e} (reason: {shutdown_reason})", exc_info=True
        )
        raise
    finally:
        # Cancel monitor
        if monitor_task:
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass

        # Shutdown
        logger.warning(f"🔍 Lifespan: Running shutdown... (reason: {shutdown_reason})")
        all_tasks = [
            t
            for t in asyncio.all_tasks()
            if not t.done() and t != asyncio.current_task()
        ]
        logger.warning(f"🔍 Tasks still running: {[t.get_name() for t in all_tasks]}")

        await proxy.shutdown()
        logger.info("🔍 Lifespan: Shutdown complete")


# Load federation config BEFORE creating app (for middleware registration)
_federation_config = load_federation_config()
logger.info(f"🔍 App: Federation mode = {_federation_config.mode.value}")

# Create FastAPI app
app = FastAPI(
    title="Universal LLM Gateway - Stargate Proxy",
    description="""
    Stargate Proxy for Universal LLM Gateway

    External Services → Port 9999 (Stargate Proxy) → Port 9998 (Universal LLM Gateway)

    Features: Intelligent defaults, parameter validation, request forwarding,
    streaming support
    """,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Store federation config in app state for telemetry endpoint
app.state.federation_config = _federation_config

# ============================================================================
# Federation Middleware (MUST be registered before app starts)
# ============================================================================
# Order matters: Last added middleware runs first (LIFO)
# These are added BEFORE CORS/RawBodyCache middleware below

if _federation_config.mode == StargateMode.REMOTE:
    logger.info("🔍 App: Adding Remote mode middleware")
    # Remote mode: Restrict endpoints + authenticate + hop counting
    app.add_middleware(RemoteModeEndpointGuard, config=_federation_config)
    app.add_middleware(FederationAuthMiddleware, config=_federation_config)
    app.add_middleware(HopCountMiddleware, config=_federation_config)
elif _federation_config.mode == StargateMode.MASTER:
    logger.info("🔍 App: Adding Master mode middleware")
    # Master mode: Sanitize headers + hop counting
    app.add_middleware(HeaderSanitizationMiddleware)
    app.add_middleware(HopCountMiddleware, config=_federation_config)
elif _federation_config.mode == StargateMode.EDGE:
    logger.info("🔍 App: Adding Edge mode middleware")
    # Edge mode: Restrict endpoints + authenticate via allowed_peers + hop counting
    app.add_middleware(EdgeModeEndpointGuard, config=_federation_config)
    app.add_middleware(EdgeFederationAuthMiddleware, config=_federation_config)
    app.add_middleware(HopCountMiddleware, config=_federation_config)
else:
    logger.info("🔍 App: Standalone mode - no federation middleware")

# ============================================================================
# Exception Handlers (Phase 3: Comprehensive Error Handling)
# ============================================================================
# These handlers ensure ALL exceptions are normalized to OpenAI API format,
# regardless of error source. Handler order matters - FastAPI applies them
# from most specific to least specific (catch-all last).
# ============================================================================


@app.exception_handler(HTTPException)
async def openai_http_exception_handler(request: Request, exc: HTTPException):
    """
    Handle FastAPI HTTPException with OpenAI format unwrapping.

    OpenAI API expects:
        {"error": {"message": "...", "type": "...", "code": "..."}}

    But FastAPI's HTTPException wraps it as:
        {"detail": {"error": {...}}}

    This handler removes the "detail" wrapper for OpenAI API compliance.

    CRITICAL: For streaming requests that error before streaming starts,
    we return the error as a streaming response (SSE error event + [DONE])
    to match client expectations and ensure proper connection closure.

    NOTE: This is the most specific handler and takes precedence over
    StarletteHTTPException and global Exception handlers.
    """
    # Extract error dict
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        error_dict = exc.detail
    else:
        # Normalize using ErrorNormalizer
        status, error_dict = ErrorNormalizer.normalize_to_openai_format(
            error=exc, default_status=exc.status_code
        )
        exc.status_code = status

    # Check if client requested streaming by looking at request body
    # This prevents clients from waiting indefinitely for streaming data
    client_wants_streaming = await _check_if_streaming_request(request)

    if client_wants_streaming and exc.status_code >= 400:
        # Return error as streaming response for proper client handling
        logger.warning(
            f"Streaming request error before stream started: {exc.status_code} "
            f"{request.method} {request.url.path}"
        )
        return await _create_streaming_error_response(error_dict, exc.status_code)

    # Non-streaming error: return regular JSON response with explicit connection close
    return JSONResponse(
        status_code=exc.status_code,
        content=error_dict,
        headers={"Connection": "close"},  # Explicit connection closure
    )


@app.exception_handler(StarletteHTTPException)
async def starlette_http_exception_handler(
    request: Request, exc: StarletteHTTPException
):
    """
    Handle Starlette HTTP exceptions (404, 405, etc.) with OpenAI format.

    These are raised by Starlette's routing layer (not found, method not allowed, etc.)
    and need to be normalized to OpenAI format for consistency.

    Examples:
    - 404 Not Found: Route doesn't exist
    - 405 Method Not Allowed: Wrong HTTP method for route
    - 406 Not Acceptable: Content negotiation failed
    """
    status, error_dict = ErrorNormalizer.normalize_to_openai_format(
        error=exc, default_status=exc.status_code
    )

    logger.info(
        "Starlette HTTP exception: %s %s %s",
        exc.status_code,
        request.method,
        request.url.path,
    )

    return JSONResponse(
        status_code=status, content=error_dict, headers={"Connection": "close"}
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Handle Pydantic validation errors with OpenAI format.

    These are raised when request body/query params don't match Pydantic models
    (invalid types, missing required fields, out-of-range values, etc.).

    Returns HTTP 400 with detailed validation error information.
    """
    status, error_dict = ErrorNormalizer.normalize_to_openai_format(
        error=exc, default_status=400
    )

    logger.warning(
        f"Validation error for {request.method} {request.url.path}: "
        f"{error_dict['error'].get('message', 'Unknown validation error')}"
    )

    return JSONResponse(
        status_code=status, content=error_dict, headers={"Connection": "close"}
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Catch-all exception handler ensuring OpenAI format for unexpected errors.

    This handler catches ANY exception that wasn't caught by more specific handlers,
    ensuring clients ALWAYS receive properly formatted error responses.

    This includes:
    - Python built-in exceptions (ValueError, TypeError, KeyError, etc.)
    - Third-party library exceptions
    - Unexpected errors in business logic
    - Unhandled edge cases

    NOTE: This is the LEAST specific handler and only runs if no other handler matched.
    """
    # Log full exception with traceback for debugging
    logger.error(
        f"Unhandled exception for {request.method} {request.url.path}: {exc}",
        exc_info=True,
    )

    # Normalize to OpenAI format
    status, error_dict = ErrorNormalizer.normalize_to_openai_format(
        error=exc, default_status=500, operation="request_processing"
    )

    return JSONResponse(
        status_code=status, content=error_dict, headers={"Connection": "close"}
    )


# CORS middleware (added first due to LIFO ordering)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Raw body cache middleware (added last = runs FIRST due to LIFO)
# CRITICAL: Preserves raw JSON before Pydantic corrupts it
app.add_middleware(RawBodyCacheMiddleware)


# Root endpoint
@app.get("/", tags=["root"])
async def root():
    return {"message": "Universal LLM Gateway Stargate Proxy", "status": "active"}


# Include all routers
app.include_router(v1.router)  # /v1/* endpoints (OpenAI API compatible)
app.include_router(api.router)  # /api/v1/* endpoints (administrative)
app.include_router(health.router)  # /health
app.include_router(schedule.router)  # /scheduler/* endpoints
app.include_router(monitoring.router)  # /api/v1/monitoring/* endpoints (Phase 4)

# Gateway forwarding under /gateway/* namespace
# Conditional mounting based on federation mode:
# - Master/Relay mode (local_edge configured): Forward to Edge Stargate
# - Standalone/Edge mode: Direct Gateway forwarding
federation_config = _federation_config
local_edge_config = federation_config.local_edge
local_edge_socket = local_edge_config.socket_path if local_edge_config else None

if local_edge_socket:
    # Master/Relay mode: Forward Gateway requests to local Edge Stargate
    from .routers.gateway_forward import create_gateway_forward_router

    gateway_forward_router = create_gateway_forward_router(
        local_edge_socket_path=local_edge_socket,
        stargate_id=federation_config.stargate_id,
        edge_api_key=local_edge_config.api_key if local_edge_config else None,
    )
    app.include_router(gateway_forward_router)
    logger.info(f"Gateway forwarding enabled via Edge: {local_edge_socket}")
else:
    # Standalone/Edge mode: Direct Gateway forwarding
    app.include_router(forwarding.router)
    logger.info("Gateway forwarding enabled (direct)")


# WebSocket endpoint for real-time monitoring (Phase 4)
@app.websocket("/ws/monitoring")
async def websocket_monitoring_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time gateway state monitoring"""
    proxy = get_proxy()

    if not proxy.websocket_manager:
        await websocket.close(code=1011, reason="WebSocket monitoring not available")
        return

    await proxy.websocket_manager.handle_client(websocket)


if __name__ == "__main__":
    import argparse

    import uvicorn

    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="Universal LLM Gateway Middleware Proxy"
    )
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=9999, help="Port to bind to")
    parser.add_argument("--log-level", default="info", help="Log level")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")

    args = parser.parse_args()

    # Run the server
    uvicorn.run(
        "proxy.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )

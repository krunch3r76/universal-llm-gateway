"""Middleware for CORS, error handling, and request logging"""

import time
from collections.abc import Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from universal_logging import get_logger

from ..core.config_loader import GatewayConfig

logger = get_logger(__name__)
api_logger = get_logger("universal_llm_gateway.api")


def setup_cors_middleware(app: FastAPI, config: GatewayConfig) -> None:
    """Setup CORS middleware"""

    allowed_origins = config.security.allowed_origins

    # Convert "*" to actual wildcard for CORS
    if "*" in allowed_origins:
        allow_origins = ["*"]
        allow_credentials = False
    else:
        allow_origins = allowed_origins
        allow_credentials = True

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=allow_credentials,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-Response-Time"],
    )

    logger.info(f"CORS middleware configured with origins: {allow_origins}")


def setup_security_middleware(app: FastAPI, config: GatewayConfig) -> None:
    """Setup security middleware"""

    # Add trusted host middleware (optional security enhancement)
    # In production, you might want to restrict this
    trusted_hosts = ["*"]  # Allow all hosts in Phase 1

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts)

    logger.info("Security middleware configured")


async def logging_middleware(request: Request, call_next: Callable) -> Response:
    """Middleware for request/response logging"""
    start_time = time.time()

    # Generate request ID for tracing
    request_id = f"req_{int(start_time * 1000000)}"

    # Log incoming request (skip for health and status endpoints to reduce noise)
    if not (
        request.url.path.endswith("/status")
        or "/status/" in request.url.path
        or request.url.path == "/health"
    ):
        logger.info(
            f"Incoming request: {request.method} {request.url.path}",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "query_params": str(request.query_params),
                "client_ip": request.client.host if request.client else "unknown",
            },
        )

    # Process request
    try:
        response = await call_next(request)

        # Calculate response time
        response_time_ms = (time.time() - start_time) * 1000

        # Add response headers
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time"] = f"{response_time_ms:.2f}ms"

        # Log response (skip for health and status endpoints to reduce noise)
        if not (
            request.url.path.endswith("/status")
            or "/status/" in request.url.path
            or request.url.path == "/health"
        ):
            api_logger.info(
                f"{request.method} {request.url.path} - {response.status_code} - {response_time_ms:.2f}ms"
            )

        return response

    except Exception as e:
        # Log error
        response_time_ms = (time.time() - start_time) * 1000

        api_logger.error(
            f"Request processing error for {request.method} {request.url.path}: {e}"
        )

        # Return error response
        enhanced_message = "Internal server error"
        if request_id:
            enhanced_message += f" Request ID: {request_id}"

        error_response = JSONResponse(
            status_code=500,
            content={
                "error": {
                    "message": enhanced_message,
                    "type": "server_error",
                    "code": "unexpected_error",
                }
            },
        )

        error_response.headers["X-Request-ID"] = request_id
        error_response.headers["X-Response-Time"] = f"{response_time_ms:.2f}ms"

        return error_response


async def rate_limiting_middleware(request: Request, call_next: Callable) -> Response:
    """Basic rate limiting middleware (Phase 1 - placeholder)"""

    # In Phase 1, this is a placeholder
    # In Phase 2, implement actual rate limiting using Redis or in-memory store

    response = await call_next(request)

    # Add rate limiting headers (informational)
    response.headers["X-RateLimit-Limit"] = "100"
    response.headers["X-RateLimit-Remaining"] = "99"
    response.headers["X-RateLimit-Reset"] = str(int(time.time()) + 60)

    return response


def setup_request_middleware(app: FastAPI, config: GatewayConfig) -> None:
    """Setup request processing middleware"""

    # Add logging middleware
    # app.middleware("http")(logging_middleware)

    # Add rate limiting if enabled
    if config.rate_limiting.enabled:
        app.middleware("http")(rate_limiting_middleware)
        logger.info("Rate limiting middleware enabled")
    else:
        logger.info("Rate limiting middleware disabled")


def setup_all_middleware(app: FastAPI, config: GatewayConfig) -> None:
    """Setup all middleware for the application"""

    # Order matters - middleware is processed in reverse order of addition
    # So add them in the order you want them to execute

    # 1. Security middleware (first to execute)
    setup_security_middleware(app, config)

    # 2. CORS middleware
    setup_cors_middleware(app, config)

    # 3. Request processing middleware (last to execute, first to see requests)
    setup_request_middleware(app, config)

    logger.info("All middleware configured successfully")

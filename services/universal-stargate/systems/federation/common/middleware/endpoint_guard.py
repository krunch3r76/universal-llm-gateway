"""
Remote mode endpoint restriction middleware.

INVARIANT: ∀ request r on Remote mode: path(r) ∈ ALLOWED_PREFIXES ∨ rejected(r)
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from universal_logging import get_logger

from ..config import FederationConfig, StargateMode

logger = get_logger(__name__)

# Allowed path prefixes in Remote mode
REMOTE_MODE_ALLOWED_PREFIXES: frozenset[str] = frozenset(
    [
        "/api/v1/federation/",
        "/health",
        "/healthz",
        "/readyz",
        "/metrics",
        "/ws/federation",
    ]
)

# Allowed path prefixes in Edge mode
EDGE_MODE_ALLOWED_PREFIXES: frozenset[str] = frozenset(
    [
        "/api/v1/federation/",
        "/api/v1/providers/",  # Provider-native cloud ingress (proxied to cloud-proxy)
        "/gateway/",  # Gateway management APIs (catalog, models, jobs)
        "/health",
        "/healthz",
        "/readyz",
        "/metrics",
        "/ws/federation",
    ]
)

# Paths requiring federation authentication
FEDERATION_AUTH_REQUIRED: frozenset[str] = frozenset(
    [
        # ALL federation HTTP endpoints
        "/api/v1/federation/",
        "/ws/federation",
    ]
)


class RemoteModeEndpointGuard(BaseHTTPMiddleware):
    """
    Middleware that restricts endpoints in Remote mode.

    INVARIANT: mode = REMOTE ⟹ only REMOTE_MODE_ALLOWED_PREFIXES accessible
    """

    def __init__(self, app, config: FederationConfig):
        super().__init__(app)
        self._config = config

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        # Only apply in Remote mode
        if self._config.mode != StargateMode.REMOTE:
            return await call_next(request)

        path = request.url.path

        # Check if path is allowed
        is_allowed = any(
            path.startswith(prefix) or path == prefix.rstrip("/")
            for prefix in REMOTE_MODE_ALLOWED_PREFIXES
        )

        if not is_allowed:
            logger.debug(f"Blocked path in Remote mode: {path}")
            return JSONResponse(
                status_code=403,
                content={
                    "error": "Endpoint disabled in remote mode",
                    "path": path,
                },
            )

        return await call_next(request)


class EdgeModeEndpointGuard(BaseHTTPMiddleware):
    """
    Middleware that restricts endpoints in Edge mode.

    INVARIANT: mode = EDGE ⟹ only EDGE_MODE_ALLOWED_PREFIXES accessible
    """

    def __init__(self, app, config: FederationConfig):
        super().__init__(app)
        self._config = config

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        # Only apply in Edge mode
        if self._config.mode != StargateMode.EDGE:
            return await call_next(request)

        path = request.url.path
        is_allowed = any(
            path.startswith(prefix) or path == prefix.rstrip("/")
            for prefix in EDGE_MODE_ALLOWED_PREFIXES
        )

        if not is_allowed:
            logger.debug(f"Blocked path in Edge mode: {path}")
            return JSONResponse(
                status_code=403,
                content={
                    "error": "Endpoint disabled in edge mode",
                    "path": path,
                },
            )

        return await call_next(request)

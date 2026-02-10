"""
Federation authentication middleware.

INVARIANT: ∀ request r to /api/v1/federation/* in Remote mode:
  authenticated(r) ⟺ header(r, "X-Federation-Source") = config.master.stargate_id
  ∧ verify_federation_key(header(r, "X-Federation-Key"), config.master.api_key)
"""

import hmac

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response
from universal_logging import get_logger

from ..config import FederationConfig, StargateMode
from ..types import HEADER_FEDERATION_KEY, HEADER_FEDERATION_SOURCE
from .endpoint_guard import FEDERATION_AUTH_REQUIRED

logger = get_logger(__name__)


def verify_federation_key(provided: str, expected: str) -> bool:
    """Constant-time comparison of API keys."""
    return hmac.compare_digest(provided.encode(), expected.encode())


class FederationAuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware for federation API key authentication.

    INVARIANT: path ∈ FEDERATION_AUTH_REQUIRED ⟹ valid_auth(request)
    """

    def __init__(self, app, config: FederationConfig):
        super().__init__(app)
        self._config = config
        # Remote mode: config.master contains the single allowed Master

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        # Only apply in Remote mode
        if self._config.mode != StargateMode.REMOTE:
            return await call_next(request)

        path = request.url.path

        # Check if auth required
        requires_auth = any(
            path.startswith(prefix) or path == prefix.rstrip("/")
            for prefix in FEDERATION_AUTH_REQUIRED
        )

        if not requires_auth:
            return await call_next(request)

        # Validate federation headers
        source = request.headers.get(HEADER_FEDERATION_SOURCE)
        key = request.headers.get(HEADER_FEDERATION_KEY)

        if not source or not key:
            logger.warning(
                "Federation auth missing headers",
                extra={"path": path, "has_source": bool(source), "has_key": bool(key)},
            )
            return JSONResponse(
                status_code=401, content={"error": "Missing federation credentials"}
            )

        # Check if source matches configured Master
        if not self._config.master:
            logger.error("Remote mode but no master configured")
            return JSONResponse(
                status_code=500, content={"error": "Server misconfigured"}
            )

        if source != self._config.master.stargate_id:
            expected = self._config.master.stargate_id
            logger.warning(f"Unknown federation source: {source} (expected {expected})")
            return JSONResponse(
                status_code=403, content={"error": "Unknown federation source"}
            )

        expected_key = self._config.master.api_key

        # Verify API key
        if not verify_federation_key(key, expected_key):
            logger.warning(f"Federation auth failed for {source}")
            return JSONResponse(
                status_code=403, content={"error": "Invalid federation key"}
            )

        # Store authenticated source in request state
        request.state.federation_source = source
        return await call_next(request)


class EdgeFederationAuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware for federation API key authentication in Edge mode.

    INVARIANT: path ∈ FEDERATION_AUTH_REQUIRED ∧ mode = EDGE ⟹
      source ∈ allowed_peers ∧ verify_federation_key(key, allowed_peers[source])
    """

    def __init__(self, app, config: FederationConfig):
        super().__init__(app)
        self._config = config
        self._allowed_peers = {p.stargate_id: p.api_key for p in config.allowed_peers}

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        # Only apply in Edge mode
        if self._config.mode != StargateMode.EDGE:
            return await call_next(request)

        path = request.url.path

        # Check if auth required
        requires_auth = any(
            path.startswith(prefix) or path == prefix.rstrip("/")
            for prefix in FEDERATION_AUTH_REQUIRED
        )
        if not requires_auth:
            return await call_next(request)

        if not self._allowed_peers:
            logger.error("Edge mode but no allowed_peers configured")
            return JSONResponse(
                status_code=500, content={"error": "Server misconfigured"}
            )

        source = request.headers.get(HEADER_FEDERATION_SOURCE)
        key = request.headers.get(HEADER_FEDERATION_KEY)

        if not source or not key:
            logger.warning(
                "Federation auth missing headers (edge mode)",
                extra={"path": path, "has_source": bool(source), "has_key": bool(key)},
            )
            return JSONResponse(
                status_code=401, content={"error": "Missing federation credentials"}
            )

        expected_key = self._allowed_peers.get(source)
        if expected_key is None:
            logger.warning(f"Unknown federation source (edge mode): {source}")
            return JSONResponse(
                status_code=403, content={"error": "Unknown federation source"}
            )

        if not verify_federation_key(key, expected_key):
            logger.warning(f"Federation auth failed for {source} (edge mode)")
            return JSONResponse(
                status_code=403, content={"error": "Invalid federation key"}
            )

        request.state.federation_source = source
        return await call_next(request)


def require_federation_auth(request: Request) -> None:
    """
    FastAPI dependency for federation authentication.

    Validates that the request has been authenticated by
    FederationAuthMiddleware by asserting middleware-set state exists.

    Args:
        request: FastAPI request with federation_source in state

    Returns:
        None (auth validated by middleware)

    Raises:
        RuntimeError: If middleware did not set federation_source
                     (indicates middleware wiring error)
    """
    # Assert middleware ran and set auth state (fail-fast on wiring errors)
    if not hasattr(request.state, "federation_source"):
        logger.error(
            "Federation auth dependency called but middleware did not set "
            "federation_source. Check middleware wiring."
        )
        raise RuntimeError(
            "Federation auth middleware not properly wired. "
            "Missing federation_source in request state."
        )

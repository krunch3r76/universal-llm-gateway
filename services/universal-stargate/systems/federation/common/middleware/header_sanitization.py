"""
Header sanitization at ingress using pure ASGI middleware.

INVARIANT: ∀ request at public ingress: strip_headers(r, X-Federation-*)

Uses pure ASGI (not BaseHTTPMiddleware) to actually modify request headers.
"""

from collections.abc import Callable
from typing import Any

from universal_logging import get_logger

from ..types import FEDERATION_HEADERS

logger = get_logger(__name__)

# Pre-compute lowercase header set for O(1) lookup
_FEDERATION_HEADERS_LOWER: frozenset[str] = frozenset(
    h.lower() for h in FEDERATION_HEADERS
)


class HeaderSanitizationMiddleware:
    """
    Pure ASGI middleware that strips federation headers at public ingress.

    INVARIANT:
      ∀ request at public_ingress ∧ ¬pipeline_internal:
        strip_headers(r, X-Federation-*)
        ∧ set(r.hop_count, 0)

    Bypass conditions (headers preserved):
      - Federation endpoints (/api/v1/federation/*, /ws/federation*)
      - Internal pipeline requests (X-Pipeline-Internal: true)

    Why pure ASGI? Starlette's BaseHTTPMiddleware provides an immutable
    Request object. To actually strip headers, we must modify the ASGI
    scope before passing to the next app.
    """

    def __init__(self, app: Callable):
        self.app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable,
        send: Callable,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        # Don't strip for federation endpoints
        if path.startswith("/api/v1/federation/") or path.startswith("/ws/federation"):
            await self.app(scope, receive, send)
            return

        original_headers: list[tuple[bytes, bytes]] = scope.get("headers", [])

        # Don't strip for internal pipeline requests (trusted origin)
        # Pipeline ProxyClient sets X-Pipeline-Internal: true alongside
        # X-Internal-Request-ID for cancellation tracking
        if _is_pipeline_internal(original_headers):
            await self.app(scope, receive, send)
            return

        # Filter out federation headers (O(n) with O(1) lookup per header)
        stripped_count = 0
        filtered_headers = []
        for name, value in original_headers:
            header_name = name.decode("latin-1").lower()
            if header_name in _FEDERATION_HEADERS_LOWER:
                stripped_count += 1
            else:
                filtered_headers.append((name, value))

        if stripped_count > 0:
            logger.debug(
                f"Stripped {stripped_count} federation headers from public request"
            )

        # Create new scope with filtered headers
        new_scope = {**scope, "headers": filtered_headers}

        # Mark that sanitization was applied (for debugging)
        new_scope.setdefault("state", {})["federation_hop_count"] = 0
        new_scope["state"]["federation_headers_stripped"] = stripped_count > 0

        await self.app(new_scope, receive, send)


def _is_pipeline_internal(headers: list[tuple[bytes, bytes]]) -> bool:
    """Check if request is from internal pipeline (X-Pipeline-Internal: true)."""
    for name, value in headers:
        if name.decode("latin-1").lower() == "x-pipeline-internal":
            return value.decode("latin-1").strip().lower() == "true"
    return False

"""
Endpoint category derivation for request routing.

Centralizes endpoint→category mapping with explicit path matching.
"""

from typing import TYPE_CHECKING

from universal_logging import get_logger

# Import existing enum from config schema
from systems.federation.common.config.schema import EndpointCategory

if TYPE_CHECKING:
    from starlette.requests import Request

logger = get_logger(__name__)


def derive_endpoint_category(
    request: "Request | None" = None, path: str | None = None
) -> EndpointCategory:
    """
    Derive endpoint category from request or explicit path.

    Args:
        request: HTTP request (preferred)
        path: Explicit path string (fallback)

    Returns:
        EndpointCategory.GENERATION, EMBEDDING, or RERANK

    Raises:
        ValueError: If path is unknown/unsupported

    INVARIANT: ∀ path: category ∈ {GENERATION, EMBEDDING, RERANK} ∨ raises ValueError
    """
    if request is not None:
        path = str(request.url.path)

    if path is None:
        raise ValueError("Either request or path must be provided")

    # Explicit path matching (not substring heuristics)
    if path.endswith("/embeddings") or "/embeddings?" in path:
        return EndpointCategory.EMBEDDING

    if path.endswith("/rerank") or "/rerank?" in path:
        return EndpointCategory.RERANK

    if path.endswith("/chat/completions") or "/chat/completions?" in path:
        return EndpointCategory.GENERATION

    if path.endswith("/completions") or "/completions?" in path:
        return EndpointCategory.GENERATION

    # Unknown path - fail loud rather than default silently
    logger.error(f"❌ Unknown endpoint path: {path} - cannot derive category")
    raise ValueError(f"Unknown endpoint path: {path}")


def endpoint_category_from_path(path: str) -> EndpointCategory:
    """
    Convenience wrapper for explicit path strings.

    Args:
        path: URL path string

    Returns:
        EndpointCategory
    """
    return derive_endpoint_category(path=path)

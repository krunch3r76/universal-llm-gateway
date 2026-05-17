"""
Gateway connection resolution for federation inference and token counting.

Provides unified `resolve_gateway_connection` with purpose-parametrized errors
to eliminate the near-duplicate `_resolve_gateway_connection` implementations
previously present in both inference.py and tokens.py.
"""

from universal_logging import get_logger

from ...common.config import FederationConfig

logger = get_logger(__name__)


def resolve_gateway_connection(
    config: FederationConfig,
    gateway_socket_path: str | None,
    gateway_url: str | None,
    *,
    purpose: str,
) -> tuple[str | None, str | None]:
    """
    Resolve gateway connection details (socket path OR HTTP URL).

    Translates container paths (/sockets/*) to host paths (/tmp/universal-sockets/*)
    to match Docker volume mount: /tmp/universal-sockets:/sockets

    Priority: explicit gateway_socket_path > explicit gateway_url > config.local_edge

    Args:
        config: Federation configuration
        gateway_socket_path: Explicit socket path override (Edge/Master modes)
        gateway_url: Explicit HTTP URL (Edge/Master modes)
        purpose: Call site descriptor for error messages (e.g. "inference")

    Returns:
        Tuple of (socket_path, http_url) — exactly one is non-None

    Raises:
        ValueError: If no connection method can be resolved for the given purpose
    """
    socket_path = None
    http_url = None

    # Resolve connection from config or parameters
    # Priority: explicit params > config.local_edge (Remote mode)
    if gateway_socket_path:
        socket_path = gateway_socket_path
    elif gateway_url:
        http_url = gateway_url
    elif config.local_edge:
        # Remote mode: local_edge points to Edge Stargate (which forwards to Gateway)
        socket_path = config.local_edge.socket_path

    # Validate at least one connection method is available
    if not socket_path and not http_url:
        raise ValueError(
            f"Gateway connection (socket or URL) not configured for {purpose}"
        )

    # Convert container socket paths to host paths if using sockets
    if socket_path and socket_path.startswith("/sockets/"):
        socket_name = socket_path.split("/")[-1]
        socket_path = f"/tmp/universal-sockets/{socket_name}"
        logger.debug(
            "Translated container socket: /sockets/%s -> %s",
            socket_name,
            socket_path,
        )

    return socket_path, http_url


__all__ = ["resolve_gateway_connection"]

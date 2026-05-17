"""
Edge relay forwarding for Remote→Edge inference topology.

Extracted from the inline `if local_edge_client:` branch inside the federation
inference handler. Provides streaming and non-streaming forwarders that
preserve the original client-lifetime and NDJSON passthrough semantics for
the relay hop (Remote stargate → Edge stargate via Unix socket).
"""

from collections.abc import AsyncIterator
from typing import Any

from transport_utils import make_async_client

from src.core.streaming.ndjson_framing import iter_ndjson_lines_bytes


async def forward_to_edge_streaming(
    edge_socket_path: str,
    body: dict[str, Any],
    headers: dict[str, str],
) -> AsyncIterator[bytes]:
    """
    Forward streaming inference request to Edge (relay topology) via Unix socket.

    Uses explicit client construction + finally:aclose to ensure the httpx
    client lifetime covers the entire consumption of the returned async
    iterator by StreamingResponse.

    INVARIANT: pure passthrough of NDJSON frames from Edge.

    Args:
        edge_socket_path: Unix socket path to the Edge stargate
        body: Full federation request envelope (contains "request" + "federation")
        headers: Pre-built headers including X-Request-ID and federation auth

    Yields:
        Raw NDJSON line bytes from Edge response

    Raises:
        httpx.HTTPStatusError: On 4xx/5xx from Edge
        httpx.RequestError: On connection failure
    """
    target_url = f"unix://{edge_socket_path}"
    client = make_async_client(target_url, timeout=1800.0)

    try:
        async with client.stream(
            "POST",
            "http://edge/api/v1/federation/inference",
            json=body,
            headers=headers,
        ) as edge_response:
            edge_response.raise_for_status()

            # NDJSON lines without decode/encode.
            async for framed_line in iter_ndjson_lines_bytes(edge_response):
                yield framed_line
    finally:
        # Close client after stream completes — covers full iterator consumption
        await client.aclose()


async def forward_to_edge_nonstreaming(
    edge_socket_path: str,
    body: dict[str, Any],
    headers: dict[str, str],
) -> dict[str, Any]:
    """
    Forward non-streaming inference request to Edge (relay topology) via Unix socket.

    INVARIANT: pure passthrough — Edge JSON response returned directly.

    Args:
        edge_socket_path: Unix socket path to the Edge stargate
        body: Full federation request envelope
        headers: Pre-built headers including X-Request-ID and federation auth

    Returns:
        Parsed JSON dict from Edge response

    Raises:
        httpx.HTTPStatusError: On 4xx/5xx from Edge
        httpx.RequestError: On connection failure
    """
    target_url = f"unix://{edge_socket_path}"

    async with make_async_client(target_url, timeout=1800.0) as client:
        edge_response = await client.post(
            "http://edge/api/v1/federation/inference",
            json=body,
            headers=headers,
        )
        edge_response.raise_for_status()
        return edge_response.json()

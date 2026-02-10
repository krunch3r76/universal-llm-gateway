"""
Cancellation sender for Master mode.

Uses WebSocket cancel when available, and falls back to HTTP cancel endpoint.

Rationale:
- In master-initiated telemetry WS topology, remotes may NOT connect inbound to
  Master's WS server, so WS cancel is not possible.
- HTTP cancel is supported on Remote/Edge federation endpoints.
"""

from __future__ import annotations

import httpx
from universal_logging import get_logger

from ...common.config import FederationConfig
from ...common.connection_manager import ConnectionManager
from ...common.types import (
    HEADER_FEDERATION_HOP_COUNT,
    HEADER_FEDERATION_KEY,
    HEADER_FEDERATION_SOURCE,
    HEADER_REQUEST_ID,
)

logger = get_logger(__name__)


class FederationCancelSender:
    """Sends cancel requests to remotes with WS→HTTP fallback."""

    def __init__(
        self,
        *,
        config: FederationConfig,
        connection_manager: ConnectionManager | None,
    ) -> None:
        self._config = config
        self._connection_manager = connection_manager

    async def send_cancel(self, remote_id: str, request_id: str) -> bool:
        """
        Cancel a request on a remote stargate.

        Args:
            remote_id: Target remote stargate ID
            request_id: Proxy request ID to cancel

        Returns:
            True if cancellation was delivered (or request already gone), else False.
        """
        if self._connection_manager:
            try:
                if await self._connection_manager.send_cancel(remote_id, request_id):
                    return True
            except Exception as e:
                logger.warning(f"WS cancel failed for {remote_id}: {e}")

        return await self._send_http_cancel(remote_id, request_id)

    async def _send_http_cancel(self, remote_id: str, request_id: str) -> bool:
        try:
            remote_url, api_key = self._lookup_remote_target(remote_id)
        except KeyError as e:
            logger.error(f"Cancel target not configured for {remote_id}: {e}")
            return False

        headers = {
            HEADER_FEDERATION_SOURCE: self._config.stargate_id,
            HEADER_FEDERATION_KEY: api_key,
            HEADER_FEDERATION_HOP_COUNT: "0",
            HEADER_REQUEST_ID: request_id,
        }

        try:
            if remote_url.startswith("unix://"):
                socket_path = remote_url[7:]
                transport = httpx.AsyncHTTPTransport(uds=socket_path)
                async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://localhost",
                    timeout=httpx.Timeout(connect=2.0, read=10.0, write=2.0, pool=2.0),
                ) as client:
                    resp = await client.delete(
                        f"/api/v1/federation/inference/{request_id}",
                        headers=headers,
                    )
            else:
                endpoint = (
                    f"{remote_url.rstrip('/')}/api/v1/federation/inference/{request_id}"
                )
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(connect=2.0, read=10.0, write=2.0, pool=2.0),
                ) as client:
                    resp = await client.delete(endpoint, headers=headers)

            if resp.status_code == 200:
                return True
            logger.warning(
                f"HTTP cancel failed for {remote_id}: status={resp.status_code}, "
                f"body={resp.text[:200]}"
            )
            return False
        except httpx.HTTPError as e:
            logger.warning(f"HTTP cancel failed for {remote_id}: {e}")
            return False

    def _lookup_remote_target(self, remote_id: str) -> tuple[str, str]:
        """
        Resolve (remote_url, api_key) for a given remote_id.

        Raises:
            KeyError if remote_id not configured.
        """
        if self._config.local_edge and self._config.local_edge.stargate_id == remote_id:
            edge = self._config.local_edge
            return f"unix://{edge.socket_path}", edge.api_key

        for remote in self._config.remotes:
            if remote.stargate_id == remote_id:
                return remote.url, remote.api_key

        raise KeyError(f"remote_id={remote_id!r} not found in config")

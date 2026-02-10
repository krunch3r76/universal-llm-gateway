"""
HTTP fetch and response parsing for telemetry polling.

INVARIANT: Exponential backoff on failures (capped at max_backoff_multiplier)
INVARIANT: Full sync requested after errors
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
from universal_logging import get_logger

if TYPE_CHECKING:
    from ....common.config.schema import FederationConfig, RemoteStargateConfig

logger = get_logger(__name__)


@dataclass
class TelemetryResponse:
    """Parsed telemetry response from remote."""

    gateway_id: str
    remote_stargate_id: str
    update_type: str  # "snapshot" or "delta"
    data: dict[str, Any]
    sequence_number: int


class TelemetryFetcher:
    """
    Fetches telemetry from a remote via HTTP.

    Handles:
    - Request construction with auth headers
    - 204 No Content (empty delta)
    - Response parsing and validation
    - Backoff tracking
    """

    def __init__(
        self,
        remote_config: RemoteStargateConfig,
        config: FederationConfig,
        http_client: httpx.AsyncClient,
    ):
        self._remote_config = remote_config
        self._config = config
        self._http_client = http_client

        self._failure_count = 0
        self._max_backoff_multiplier = 8
        self._needs_full_sync = True  # Request full snapshot on first poll
        self._last_gateway_id: str | None = None

    @property
    def last_gateway_id(self) -> str | None:
        """Gateway ID from last successful response."""
        return self._last_gateway_id

    async def fetch(self) -> TelemetryResponse | None:
        """
        Fetch telemetry from remote.

        Returns:
            TelemetryResponse if data received, None if 204 No Content

        Raises:
            Exception: On HTTP errors or connection failures
        """
        url = self._build_url()
        headers = self._build_headers()

        logger.debug(f"📡 Fetching telemetry: {url} (full={self._needs_full_sync})")

        response = await self._http_client.get(url, headers=headers, timeout=10.0)

        if response.status_code == 204:
            self._failure_count = 0
            return None

        if response.status_code != 200:
            raise Exception(f"HTTP {response.status_code}")

        return self._parse_response(response)

    def _build_url(self) -> str:
        """Build telemetry endpoint URL."""
        url = f"{self._remote_config.url.rstrip('/')}/api/v1/federation/telemetry"
        if self._needs_full_sync:
            url += "?full=true"
        return url

    def _build_headers(self) -> dict[str, str]:
        """Build request headers with auth."""
        return {
            "X-Federation-Source": self._config.stargate_id,
            "X-Federation-Key": self._remote_config.api_key,
        }

    def _parse_response(self, response: httpx.Response) -> TelemetryResponse | None:
        """Parse and validate response."""
        data = response.json()

        gateway_id = data.get("gateway_id")
        if not gateway_id:
            logger.warning(
                f"Missing gateway_id in response from {self._remote_config.stargate_id}"
            )
            return None

        self._last_gateway_id = gateway_id
        self._needs_full_sync = False

        return TelemetryResponse(
            gateway_id=gateway_id,
            remote_stargate_id=self._remote_config.stargate_id,
            update_type=data.get("type", "delta"),
            data=data,
            sequence_number=data.get("sequence_number", 0),
        )

    def reset_backoff(self) -> None:
        """Reset failure count on success."""
        self._failure_count = 0

    def get_backoff_interval(self) -> int:
        """Get current backoff interval in ms."""
        self._failure_count += 1
        multiplier = min(2 ** (self._failure_count - 1), self._max_backoff_multiplier)
        interval = self._remote_config.telemetry_poll_interval_ms * multiplier
        # FED-11: Cap at 30000ms
        return min(interval, 30000)

    def request_full_sync(self) -> None:
        """Request full snapshot on next poll."""
        self._needs_full_sync = True

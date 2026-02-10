"""Gateway type definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gateway_client import GatewayClient, GatewayConfig


@dataclass
class GatewayInstance:
    """Gateway instance with performance tracking.

    Health is derived from WebSocket connection state via client.is_connected().
    """

    client: GatewayClient
    config: GatewayConfig
    response_times: list[float] = field(default_factory=list)
    failed_requests: int = 0
    total_requests: int = 0

    @property
    def average_response_time(self) -> float:
        """Calculate average response time from last 10 requests."""
        if not self.response_times:
            return 0.0
        recent = self.response_times[-10:]
        return sum(recent) / len(recent)

    @property
    def success_rate(self) -> float:
        """Calculate success rate."""
        if self.total_requests == 0:
            return 1.0
        return (self.total_requests - self.failed_requests) / self.total_requests

    def record_request_time(self, duration: float, success: bool = True) -> None:
        """Record request performance."""
        self.response_times.append(duration)
        if len(self.response_times) > 50:
            self.response_times = self.response_times[-50:]

        self.total_requests += 1
        if not success:
            self.failed_requests += 1

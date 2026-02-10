"""
Centralized gateway logging through unified event system.

This module provides a single logging point for all gateway state changes,
eliminating scattered ERROR/WARNING logs throughout the codebase.
"""

import time
from collections import defaultdict
from typing import Any

from universal_event_bus import Event, EventBus
from universal_logging import get_logger

from .events import GATEWAY_STATE_CHANGED

logger = get_logger(__name__)


class GatewayLogger:
    """
    Centralized logger that subscribes to GATEWAY_STATE_CHANGED events.

    Features:
    - Single logging point for all gateway state changes
    - Rate limiting to prevent log flooding
    - Structured log format with gateway context
    - Configurable log levels per transition type
    """

    def __init__(
        self,
        event_bus: EventBus,
        rate_limit_window: float = 60.0,  # 1 minute window
        max_logs_per_window: int = 5,  # Max 5 logs per transition type per minute
        log_connectivity_changes: bool = True,
        log_health_changes: bool = True,
    ):
        """
        Initialize the centralized gateway logger.

        Args:
            event_bus: EventBus to subscribe to GATEWAY_STATE_CHANGED events
            rate_limit_window: Time window for rate limiting (seconds)
            max_logs_per_window: Maximum logs per transition type within window
            log_connectivity_changes: Whether to log connectivity transitions
            log_health_changes: Whether to log health transitions
        """
        self.event_bus = event_bus
        self.rate_limit_window = rate_limit_window
        self.max_logs_per_window = max_logs_per_window
        self.log_connectivity_changes = log_connectivity_changes
        self.log_health_changes = log_health_changes

        # Rate limiting tracking: {(gateway_url, transition_type): [timestamp1, timestamp2, ...]}
        self._log_timestamps: dict[tuple[str, str], list[float]] = defaultdict(list)

        # Subscribe to gateway state change events
        self.event_bus.subscribe_async(GATEWAY_STATE_CHANGED, self._handle_state_change)

        logger.info("Initialized centralized GatewayLogger")

    def stop(self) -> None:
        """Stop the logger"""
        # Note: EventBus handlers persist for bus lifetime (no unsubscribe support)
        # Just log that we're stopping, no need to unsubscribe
        logger.info("Stopped centralized GatewayLogger")

    async def _handle_state_change(self, event: Event) -> None:
        """
        Handle GATEWAY_STATE_CHANGED events and log appropriately.

        Args:
            event: Event with GATEWAY_STATE_CHANGED signal
        """
        payload = event.payload

        # Extract state information
        url = payload.get("url")
        connectivity = payload.get("connectivity")
        health = payload.get("health")
        previous_connectivity = payload.get("previous_connectivity")
        previous_health = payload.get("previous_health")
        transition_type = payload.get("transition_type")
        check_duration_ms = payload.get("check_duration_ms", 0)

        # Determine if we should log this transition
        if not self._should_log(url, transition_type):
            return

        # Format and log based on transition type
        if transition_type == "initial":
            self._log_initial_state(url, connectivity, health, check_duration_ms)
        elif transition_type == "connectivity_only":
            if self.log_connectivity_changes:
                self._log_connectivity_transition(
                    url, previous_connectivity, connectivity, check_duration_ms
                )
        elif transition_type == "health_only":
            if self.log_health_changes:
                self._log_health_transition(
                    url, previous_health, health, check_duration_ms
                )
        elif transition_type == "both":
            # Log both transitions
            if self.log_connectivity_changes:
                self._log_connectivity_transition(
                    url, previous_connectivity, connectivity, check_duration_ms
                )
            if self.log_health_changes:
                self._log_health_transition(
                    url, previous_health, health, check_duration_ms
                )

    def _should_log(self, gateway_url: str, transition_type: str) -> bool:
        """
        Rate limiting check to prevent log flooding.

        Args:
            gateway_url: Gateway URL
            transition_type: Type of state transition

        Returns:
            bool: True if this transition should be logged
        """
        current_time = time.time()
        key = (gateway_url, transition_type)

        # Clean up old timestamps outside the window
        self._log_timestamps[key] = [
            ts
            for ts in self._log_timestamps[key]
            if current_time - ts < self.rate_limit_window
        ]

        # Check if we've exceeded the rate limit
        if len(self._log_timestamps[key]) >= self.max_logs_per_window:
            # Rate limited - don't log
            return False

        # Record this log
        self._log_timestamps[key].append(current_time)
        return True

    def _log_initial_state(
        self,
        url: str,
        connectivity: str,
        health: str,
        check_duration_ms: int,
    ) -> None:
        """Log initial gateway state discovery."""
        logger.info(
            f"Gateway state initialized: {url} | "
            f"connectivity={connectivity}, health={health} | "
            f"check_duration={check_duration_ms}ms"
        )

    def _log_connectivity_transition(
        self,
        url: str,
        previous: str | None,
        current: str,
        check_duration_ms: int,
    ) -> None:
        """Log gateway connectivity state transition."""
        if current == "unreachable":
            logger.warning(
                f"Gateway became unreachable: {url} | "
                f"previous={previous} | "
                f"check_duration={check_duration_ms}ms"
            )
        else:
            logger.info(
                f"Gateway now reachable: {url} | "
                f"previous={previous} | "
                f"check_duration={check_duration_ms}ms"
            )

    def _log_health_transition(
        self,
        url: str,
        previous: str | None,
        current: str,
        check_duration_ms: int,
    ) -> None:
        """Log gateway health state transition."""
        if current == "healthy":
            logger.info(
                f"Gateway now healthy: {url} | "
                f"previous={previous} | "
                f"check_duration={check_duration_ms}ms"
            )
        elif current == "unhealthy":
            logger.warning(
                f"Gateway now unhealthy: {url} | "
                f"previous={previous} | "
                f"check_duration={check_duration_ms}ms"
            )
        else:  # unknown
            logger.debug(
                f"Gateway health unknown: {url} | "
                f"previous={previous} | "
                f"check_duration={check_duration_ms}ms"
            )

    def get_statistics(self) -> dict[str, Any]:
        """
        Get logging statistics for monitoring.

        Returns:
            Dict with logging stats (log counts per gateway/transition)
        """
        current_time = time.time()
        stats: dict[str, Any] = {
            "rate_limit_window": self.rate_limit_window,
            "max_logs_per_window": self.max_logs_per_window,
            "log_counts": {},
        }

        for (gateway_url, transition_type), timestamps in self._log_timestamps.items():
            # Count logs within current window
            active_logs = [
                ts for ts in timestamps if current_time - ts < self.rate_limit_window
            ]

            key = f"{gateway_url}:{transition_type}"
            stats["log_counts"][key] = len(active_logs)

        return stats

    def reset_statistics(self) -> None:
        """Reset all logging statistics (useful for testing)."""
        self._log_timestamps.clear()
        logger.debug("Reset GatewayLogger statistics")

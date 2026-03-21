"""
Monitoring consumer — real-time gateway and model state for dashboards.

Subscribes to gateway state transitions and model lifecycle events,
maintains state for monitoring dashboards, WebSocket push, and metrics.
"""

import asyncio
import time
from collections import deque
from datetime import datetime

from universal_event_bus import Event, EventBus
from universal_logging import get_logger

from ..events import (
    GATEWAY_STATE_CHANGED,
    MODEL_EXECUTION_COMPLETED,
    MODEL_EXECUTION_STARTED,
    MODEL_LOADED,
    MODEL_LOADING_STARTED,
    MODEL_UNLOADED,
)
from ..gateway_state import ConnectivityState, HealthState

logger = get_logger(__name__)


class StateTransition:
    """Represents a gateway state transition for historical tracking"""

    def __init__(
        self,
        url: str,
        from_state: dict[str, str],
        to_state: dict[str, str],
        timestamp: float,
    ):
        self.url = url
        self.from_state = from_state
        self.to_state = to_state
        self.timestamp = timestamp
        self.datetime = datetime.fromtimestamp(timestamp)

    def to_dict(self) -> dict:
        """Convert transition to dictionary for API responses"""
        return {
            "url": self.url,
            "from_connectivity": self.from_state.get("connectivity"),
            "from_health": self.from_state.get("health"),
            "to_connectivity": self.to_state["connectivity"],
            "to_health": self.to_state["health"],
            "timestamp": self.timestamp,
            "datetime": self.datetime.isoformat(),
        }


class MonitoringConsumer:
    """
    Consumes gateway state events for monitoring and dashboard updates.

    Provides:
    - Real-time gateway state tracking
    - Historical state transition tracking
    - WebSocket update notifications
    - Prometheus metrics export
    """

    def __init__(self, event_bus: EventBus, history_size: int = 1000):
        """
        Initialize monitoring consumer.

        Args:
            event_bus: EventBus instance for event subscription
            history_size: Maximum number of transitions to keep in history
        """
        self.event_bus = event_bus
        self.history_size = history_size

        # Current state tracking
        self._current_states: dict[str, dict[str, str]] = {}
        self._state_timestamps: dict[str, float] = {}

        # Historical tracking
        self._state_transitions: deque[StateTransition] = deque(maxlen=history_size)
        self._transition_counts: dict[str, int] = {}

        # Uptime tracking
        self._uptime_start: dict[str, float] = {}
        self._downtime_start: dict[str, float] = {}
        self._total_uptime: dict[str, float] = {}
        self._total_downtime: dict[str, float] = {}

        self._websocket_subscribers: list[asyncio.Queue] = []

    def start(self):
        """Start consuming gateway state and model lifecycle events."""
        self.event_bus.subscribe_async(GATEWAY_STATE_CHANGED, self._handle_state_change)
        self.event_bus.subscribe_async(MODEL_LOADED, self._handle_model_event)
        self.event_bus.subscribe_async(MODEL_UNLOADED, self._handle_model_event)
        self.event_bus.subscribe_async(MODEL_LOADING_STARTED, self._handle_model_event)
        self.event_bus.subscribe_async(
            MODEL_EXECUTION_STARTED, self._handle_model_event
        )
        self.event_bus.subscribe_async(
            MODEL_EXECUTION_COMPLETED, self._handle_model_event
        )
        logger.info("✅ MonitoringConsumer started")

    def stop(self):
        """Stop consuming events"""
        # Note: EventBus handlers persist for bus lifetime (no unsubscribe support)
        logger.info("MonitoringConsumer stopped")

    async def _handle_state_change(self, event: Event):
        """Handle gateway state change events for monitoring"""
        payload = event.payload
        url = payload["url"]
        connectivity = payload["connectivity"]
        health = payload["health"]
        previous_connectivity = payload.get("previous_connectivity")
        previous_health = payload.get("previous_health")

        current_time = time.time()

        # Track previous state for transition history
        previous_state = self._current_states.get(
            url, {"connectivity": previous_connectivity, "health": previous_health}
        )

        # Update current state
        new_state = {"connectivity": connectivity, "health": health}
        self._current_states[url] = new_state
        self._state_timestamps[url] = current_time

        # Record transition in history
        if previous_state.get("connectivity") or previous_state.get("health"):
            transition = StateTransition(url, previous_state, new_state, current_time)
            self._state_transitions.append(transition)

            # Count transitions by type
            transition_key = f"{url}:{connectivity}:{health}"
            self._transition_counts[transition_key] = (
                self._transition_counts.get(transition_key, 0) + 1
            )

        # Update uptime/downtime tracking
        is_available = (
            connectivity == ConnectivityState.REACHABLE.value
            and health == HealthState.HEALTHY.value
        )

        if is_available:
            # Gateway became available
            if url in self._downtime_start:
                downtime_duration = current_time - self._downtime_start[url]
                self._total_downtime[url] = (
                    self._total_downtime.get(url, 0) + downtime_duration
                )
                del self._downtime_start[url]

            if url not in self._uptime_start:
                self._uptime_start[url] = current_time
        else:
            # Gateway became unavailable
            if url in self._uptime_start:
                uptime_duration = current_time - self._uptime_start[url]
                self._total_uptime[url] = (
                    self._total_uptime.get(url, 0) + uptime_duration
                )
                del self._uptime_start[url]

            if url not in self._downtime_start:
                self._downtime_start[url] = current_time

        # Notify WebSocket subscribers (outside critical section)
        await self._notify_websocket_subscribers(
            {
                "type": "state_change",
                "url": url,
                "connectivity": connectivity,
                "health": health,
                "timestamp": current_time,
            }
        )

    async def _handle_model_event(self, event: Event) -> None:
        """Forward model lifecycle events to WebSocket subscribers.

        Translates internal EventBus model signals into a uniform
        ``model_status_change`` message shape for dashboard consumers.
        """
        payload = event.payload
        signal_to_status = {
            MODEL_LOADED: "loaded",
            MODEL_UNLOADED: "unloaded",
            MODEL_LOADING_STARTED: "loading",
            MODEL_EXECUTION_STARTED: "busy",
            MODEL_EXECUTION_COMPLETED: "idle",
        }
        await self._notify_websocket_subscribers(
            {
                "type": "model_status_change",
                "model_id": payload.get("model_id", ""),
                "node_id": payload.get("url", ""),
                "status": signal_to_status.get(event.signal, event.signal),
                "signal": event.signal,
                "timestamp": time.time(),
            }
        )

    async def _notify_websocket_subscribers(self, message: dict):
        """Notify all WebSocket subscribers of state changes"""
        # Put message in all subscriber queues
        for queue in self._websocket_subscribers[
            :
        ]:  # Copy to avoid modification during iteration
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                # Remove slow subscribers
                self._websocket_subscribers.remove(queue)
                logger.warning("Removed slow WebSocket subscriber")

    def get_current_states(self) -> dict[str, dict[str, str]]:
        """Get current states for all gateways"""
        return self._current_states.copy()

    def get_state_history(self, limit: int | None = None) -> list[dict]:
        """
        Get historical state transitions.

        Args:
            limit: Maximum number of transitions to return (most recent first)

        Returns:
            List of transition dictionaries
        """
        transitions = list(self._state_transitions)
        transitions.reverse()  # Most recent first

        if limit:
            transitions = transitions[:limit]

        return [t.to_dict() for t in transitions]

    def get_uptime_stats(self, url: str) -> dict[str, float]:
        """
        Get uptime/downtime statistics for a gateway.

        Args:
            url: Gateway URL

        Returns:
            Dictionary with uptime and downtime statistics
        """
        current_time = time.time()

        total_uptime = self._total_uptime.get(url, 0)
        total_downtime = self._total_downtime.get(url, 0)

        # Add current period
        if url in self._uptime_start:
            total_uptime += current_time - self._uptime_start[url]
        elif url in self._downtime_start:
            total_downtime += current_time - self._downtime_start[url]

        total_time = total_uptime + total_downtime
        uptime_percentage = (total_uptime / total_time * 100) if total_time > 0 else 0

        return {
            "total_uptime_seconds": total_uptime,
            "total_downtime_seconds": total_downtime,
            "uptime_percentage": uptime_percentage,
            "currently_up": url in self._uptime_start,
        }

    def get_prometheus_metrics(self) -> str:
        """
        Export metrics in Prometheus format.

        Returns:
            Prometheus-formatted metrics string
        """
        metrics = []

        # Gateway state gauge (1 = available, 0 = unavailable)
        metrics.append("# HELP gateway_state Gateway availability state")
        metrics.append("# TYPE gateway_state gauge")
        for url, state in self._current_states.items():
            is_available = (
                state["connectivity"] == ConnectivityState.REACHABLE.value
                and state["health"] == HealthState.HEALTHY.value
            )
            value = 1 if is_available else 0
            metrics.append(f'gateway_state{{url="{url}"}} {value}')

        # Uptime metrics
        metrics.append("# HELP gateway_uptime_seconds Total uptime in seconds")
        metrics.append("# TYPE gateway_uptime_seconds counter")
        for url in self._current_states.keys():
            stats = self.get_uptime_stats(url)
            uptime = stats["total_uptime_seconds"]
            metrics.append(f'gateway_uptime_seconds{{url="{url}"}} {uptime:.2f}')

        # Transition counts
        metrics.append("# HELP gateway_state_transitions_total Total state transitions")
        metrics.append("# TYPE gateway_state_transitions_total counter")
        for key, count in self._transition_counts.items():
            url, connectivity, health = key.split(":", 2)
            metrics.append(
                f'gateway_state_transitions_total{{url="{url}",'
                f'connectivity="{connectivity}",health="{health}"}} {count}'
            )

        return "\n".join(metrics)

    def get_dashboard_summary(self) -> dict:
        """
        Get comprehensive summary for monitoring dashboard.

        Returns:
            Dictionary with current states, history, and statistics
        """
        return {
            "current_states": self.get_current_states(),
            "recent_transitions": self.get_state_history(limit=10),
            "uptime_stats": {
                url: self.get_uptime_stats(url) for url in self._current_states.keys()
            },
            "total_gateways": len(self._current_states),
            "available_gateways": sum(
                1
                for state in self._current_states.values()
                if state["connectivity"] == ConnectivityState.REACHABLE.value
                and state["health"] == HealthState.HEALTHY.value
            ),
        }

    async def subscribe_websocket(self) -> asyncio.Queue:
        """
        Subscribe to WebSocket updates.

        Returns:
            Queue that will receive state change notifications
        """
        queue = asyncio.Queue(maxsize=100)
        self._websocket_subscribers.append(queue)
        logger.info("New WebSocket subscriber connected")
        return queue

    async def unsubscribe_websocket(self, queue: asyncio.Queue):
        """Unsubscribe from WebSocket updates"""
        if queue in self._websocket_subscribers:
            self._websocket_subscribers.remove(queue)
            logger.info("WebSocket subscriber disconnected")

"""
Metrics consumer that tracks operational analytics for gateway state changes.

This consumer subscribes to GATEWAY_STATE_CHANGED events and provides
statistical analysis of gateway behavior, performance, and reliability.
"""

import time
from collections import defaultdict

from universal_event_bus import Event, EventBus
from universal_logging import get_logger

from ..events import GATEWAY_STATE_CHANGED
from ..gateway_state import ConnectivityState, HealthState

logger = get_logger(__name__)


class MetricsConsumer:
    """
    Consumes gateway state events for metrics collection and analysis.

    Tracks:
    - State transition frequencies
    - Average downtime/uptime per gateway
    - State change patterns and timing
    - Request success correlation with gateway state
    """

    def __init__(self, event_bus: EventBus):
        """
        Initialize metrics consumer.

        Args:
            event_bus: EventBus instance for event subscription
        """
        self.event_bus = event_bus

        # Transition tracking
        self._transition_counts: dict[str, dict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )
        self._transition_times: dict[str, list[float]] = defaultdict(list)

        # Downtime/uptime tracking
        self._downtime_periods: dict[str, list[float]] = defaultdict(list)
        self._uptime_periods: dict[str, list[float]] = defaultdict(list)
        self._last_downtime_start: dict[str, float] = {}
        self._last_uptime_start: dict[str, float] = {}

        # Performance metrics
        self._state_check_durations: dict[str, list[int]] = defaultdict(
            list
        )  # milliseconds
        self._consecutive_failures: dict[str, int] = defaultdict(int)
        self._max_consecutive_failures: dict[str, int] = defaultdict(int)

        # Time-based metrics
        self._hourly_transitions: dict[int, int] = defaultdict(int)
        self._daily_transitions: dict[str, int] = defaultdict(int)

        self._start_time = time.time()

    def start(self):
        """Start consuming events"""
        # Subscribe to unified state change events (synchronous in EventBus v0.2.0)
        self.event_bus.subscribe_async(GATEWAY_STATE_CHANGED, self._handle_state_change)
        logger.info("✅ MetricsConsumer started")

    def stop(self):
        """Stop consuming events"""
        # Note: EventBus handlers persist for bus lifetime (no unsubscribe support)
        logger.info("MetricsConsumer stopped")

    async def _handle_state_change(self, event: Event):
        """Handle gateway state change events for metrics collection"""
        payload = event.payload
        url = payload["url"]
        connectivity = payload["connectivity"]
        health = payload["health"]
        check_duration_ms = payload.get("check_duration_ms", 0)

        current_time = time.time()

        # Track transition counts by type
        transition_key = f"{connectivity}:{health}"
        self._transition_counts[url][transition_key] += 1
        self._transition_times[url].append(current_time)

        # Track state check performance
        self._state_check_durations[url].append(check_duration_ms)
        if len(self._state_check_durations[url]) > 1000:
            # Keep last 1000 measurements
            self._state_check_durations[url] = self._state_check_durations[url][-1000:]

        # Track downtime/uptime periods
        is_available = (
            connectivity == ConnectivityState.REACHABLE.value
            and health == HealthState.HEALTHY.value
        )

        if is_available:
            # Gateway became available
            if url in self._last_downtime_start:
                downtime_duration = current_time - self._last_downtime_start[url]
                self._downtime_periods[url].append(downtime_duration)
                del self._last_downtime_start[url]

                # Reset consecutive failure count
                self._consecutive_failures[url] = 0

            if url not in self._last_uptime_start:
                self._last_uptime_start[url] = current_time
        else:
            # Gateway became unavailable
            if url in self._last_uptime_start:
                uptime_duration = current_time - self._last_uptime_start[url]
                self._uptime_periods[url].append(uptime_duration)
                del self._last_uptime_start[url]

            if url not in self._last_downtime_start:
                self._last_downtime_start[url] = current_time

            # Track consecutive failures
            self._consecutive_failures[url] += 1
            if self._consecutive_failures[url] > self._max_consecutive_failures[url]:
                self._max_consecutive_failures[url] = self._consecutive_failures[url]

        # Time-based tracking
        current_hour = int(current_time / 3600) % 24
        self._hourly_transitions[current_hour] += 1

        current_day = time.strftime("%Y-%m-%d", time.localtime(current_time))
        self._daily_transitions[current_day] += 1

    def get_transition_statistics(self, url: str) -> dict:
        """
        Get transition statistics for a gateway.

        Args:
            url: Gateway URL

        Returns:
            Dictionary with transition statistics
        """
        transitions = self._transition_counts.get(url, {})
        total_transitions = sum(transitions.values())

        return {
            "total_transitions": total_transitions,
            "transitions_by_state": dict(transitions),
            "average_time_between_transitions": self._calculate_avg_transition_interval(
                url
            ),
            "max_consecutive_failures": self._max_consecutive_failures.get(url, 0),
        }

    def get_downtime_statistics(self, url: str) -> dict:
        """
        Get downtime statistics for a gateway.

        Args:
            url: Gateway URL

        Returns:
            Dictionary with downtime statistics
        """
        downtime_periods = self._downtime_periods.get(url, [])

        if not downtime_periods:
            return {
                "total_downtime_events": 0,
                "average_downtime_seconds": 0,
                "max_downtime_seconds": 0,
                "min_downtime_seconds": 0,
            }

        return {
            "total_downtime_events": len(downtime_periods),
            "average_downtime_seconds": sum(downtime_periods) / len(downtime_periods),
            "max_downtime_seconds": max(downtime_periods),
            "min_downtime_seconds": min(downtime_periods),
            "total_downtime_seconds": sum(downtime_periods),
        }

    def get_uptime_statistics(self, url: str) -> dict:
        """
        Get uptime statistics for a gateway.

        Args:
            url: Gateway URL

        Returns:
            Dictionary with uptime statistics
        """
        uptime_periods = self._uptime_periods.get(url, [])

        if not uptime_periods:
            return {
                "total_uptime_events": 0,
                "average_uptime_seconds": 0,
                "max_uptime_seconds": 0,
                "min_uptime_seconds": 0,
            }

        return {
            "total_uptime_events": len(uptime_periods),
            "average_uptime_seconds": sum(uptime_periods) / len(uptime_periods),
            "max_uptime_seconds": max(uptime_periods),
            "min_uptime_seconds": min(uptime_periods),
            "total_uptime_seconds": sum(uptime_periods),
        }

    def get_performance_metrics(self, url: str) -> dict:
        """
        Get performance metrics for a gateway.

        Args:
            url: Gateway URL

        Returns:
            Dictionary with performance metrics
        """
        check_durations = self._state_check_durations.get(url, [])

        if not check_durations:
            return {
                "total_checks": 0,
                "average_check_time_ms": 0,
                "max_check_time_ms": 0,
                "min_check_time_ms": 0,
            }

        return {
            "total_checks": len(check_durations),
            "average_check_time_ms": sum(check_durations) / len(check_durations),
            "max_check_time_ms": max(check_durations),
            "min_check_time_ms": min(check_durations),
            "p95_check_time_ms": self._calculate_percentile(check_durations, 0.95),
            "p99_check_time_ms": self._calculate_percentile(check_durations, 0.99),
        }

    def get_time_based_metrics(self) -> dict:
        """
        Get time-based metrics across all gateways.

        Returns:
            Dictionary with hourly and daily transition patterns
        """
        return {
            "hourly_transitions": dict(self._hourly_transitions),
            "daily_transitions": dict(self._daily_transitions),
            "peak_hour": (
                max(self._hourly_transitions.items(), key=lambda x: x[1])[0]
                if self._hourly_transitions
                else None
            ),
        }

    def get_comprehensive_metrics(self, url: str) -> dict:
        """
        Get comprehensive metrics for a gateway.

        Args:
            url: Gateway URL

        Returns:
            Dictionary with all available metrics
        """
        return {
            "transitions": self.get_transition_statistics(url),
            "downtime": self.get_downtime_statistics(url),
            "uptime": self.get_uptime_statistics(url),
            "performance": self.get_performance_metrics(url),
            "reliability_score": self._calculate_reliability_score(url),
        }

    def get_all_gateway_metrics(self) -> dict[str, dict]:
        """
        Get metrics for all tracked gateways.

        Returns:
            Dictionary mapping gateway URL to comprehensive metrics
        """
        # Get all unique gateway URLs from any tracking dict
        all_urls = set()
        all_urls.update(self._transition_counts.keys())
        all_urls.update(self._downtime_periods.keys())
        all_urls.update(self._uptime_periods.keys())

        return {url: self.get_comprehensive_metrics(url) for url in all_urls}

    def get_system_wide_metrics(self) -> dict:
        """
        Get system-wide metrics across all gateways.

        Returns:
            Dictionary with aggregated system metrics
        """
        all_metrics = self.get_all_gateway_metrics()

        if not all_metrics:
            return {
                "total_gateways": 0,
                "total_transitions": 0,
                "average_reliability": 0,
                "time_patterns": self.get_time_based_metrics(),
            }

        total_transitions = sum(
            m["transitions"]["total_transitions"] for m in all_metrics.values()
        )

        avg_reliability = sum(
            m["reliability_score"] for m in all_metrics.values()
        ) / len(all_metrics)

        return {
            "total_gateways": len(all_metrics),
            "total_transitions": total_transitions,
            "average_reliability": avg_reliability,
            "time_patterns": self.get_time_based_metrics(),
            "uptime_since": self._start_time,
            "metrics_collection_duration_seconds": time.time() - self._start_time,
        }

    def _calculate_avg_transition_interval(self, url: str) -> float:
        """Calculate average time between transitions"""
        transition_times = self._transition_times.get(url, [])

        if len(transition_times) < 2:
            return 0

        intervals = [
            transition_times[i] - transition_times[i - 1]
            for i in range(1, len(transition_times))
        ]

        return sum(intervals) / len(intervals) if intervals else 0

    def _calculate_percentile(self, values: list[float], percentile: float) -> float:
        """Calculate percentile of a list of values"""
        if not values:
            return 0

        sorted_values = sorted(values)
        index = int(len(sorted_values) * percentile)
        return sorted_values[min(index, len(sorted_values) - 1)]

    def _calculate_reliability_score(self, url: str) -> float:
        """
        Calculate reliability score (0-100) based on uptime/downtime ratio.

        Args:
            url: Gateway URL

        Returns:
            Reliability score between 0 and 100
        """
        uptime_stats = self.get_uptime_statistics(url)
        downtime_stats = self.get_downtime_statistics(url)

        total_uptime = uptime_stats["total_uptime_seconds"]
        total_downtime = downtime_stats["total_downtime_seconds"]

        if total_uptime + total_downtime == 0:
            return 100.0  # No data yet, assume perfect

        reliability = (total_uptime / (total_uptime + total_downtime)) * 100
        return round(reliability, 2)

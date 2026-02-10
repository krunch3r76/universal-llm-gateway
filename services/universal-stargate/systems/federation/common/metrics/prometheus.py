"""
Prometheus metrics for federation.

INVARIANT: ∀ failure_mode f: ∃ metric m: detects(m, f)

All metrics use federation_ prefix per naming conventions.
"""

from prometheus_client import Counter, Gauge, Histogram
from universal_logging import get_logger

logger = get_logger(__name__)


class FederationMetrics:
    """
    Prometheus metrics collector for federation.

    Instantiate once at startup and use throughout the application.
    """

    def __init__(self) -> None:
        # Connection & Lifecycle
        self.ws_connected = Gauge(
            "federation_ws_connected",
            "WebSocket connection state (1=connected, 0=disconnected)",
            ["remote_id"],
        )
        self.ws_reconnect_total = Counter(
            "federation_ws_reconnect_total",
            "WebSocket reconnection count by reason",
            ["remote_id", "reason"],
        )
        self.identity_collision_total = Counter(
            "federation_identity_collision_total",
            "Duplicate identity connection attempts",
            ["stargate_id"],
        )
        self.auth_failure_total = Counter(
            "federation_auth_failure_total",
            "Authentication failures by cause",
            ["remote_id", "reason"],
        )
        self.protocol_mismatch_total = Counter(
            "federation_protocol_mismatch_total",
            "Protocol version mismatches",
            ["local_version", "remote_version"],
        )

        # Telemetry
        self.telemetry_received_total = Counter(
            "federation_telemetry_received_total",
            "Telemetry events received by type",
            ["remote_id", "signal"],
        )
        self.telemetry_dropped_total = Counter(
            "federation_telemetry_dropped_total",
            "Telemetry events dropped (backpressure)",
            ["remote_id", "reason"],
        )
        self.telemetry_queue_depth = Gauge(
            "federation_telemetry_queue_depth",
            "Current telemetry queue depth",
            ["remote_id"],
        )
        self.last_pong_age_ms = Gauge(
            "federation_last_pong_age_ms",
            "Time since last pong in milliseconds (VPS idle detection)",
            ["remote_id"],
        )

        # Request handling
        self.request_total = Counter(
            "federation_request_total",
            "Forwarded requests by status",
            ["remote_id", "status"],
        )
        self.request_latency_ms = Histogram(
            "federation_request_latency_ms",
            "End-to-end request latency in milliseconds",
            ["remote_id"],
            buckets=[10, 50, 100, 250, 500, 1000, 2500, 5000, 10000],
        )
        self.ttft_ms = Histogram(
            "federation_ttft_ms",
            "Time to first token in milliseconds (streaming)",
            ["remote_id"],
            buckets=[50, 100, 250, 500, 1000, 2500, 5000],
        )
        self.active_requests = Gauge(
            "federation_active_requests",
            "Currently active requests",
            ["remote_id"],
        )

        # Cancellation
        self.cancel_requested_total = Counter(
            "federation_cancel_requested_total",
            "Cancel requests issued",
            ["remote_id"],
        )
        self.cancel_delivered_total = Counter(
            "federation_cancel_delivered_total",
            "Cancel requests successfully delivered",
            ["remote_id"],
        )
        self.cancel_failed_total = Counter(
            "federation_cancel_failed_total",
            "Cancel requests that failed",
            ["remote_id", "reason"],
        )
        self.pending_cancels = Gauge(
            "federation_pending_cancels",
            "Cancels pending reconnect replay",
            ["remote_id"],
        )

        # Security
        self.headers_stripped_total = Counter(
            "federation_headers_stripped_total",
            "Federation headers stripped at ingress",
        )
        self.endpoint_blocked_total = Counter(
            "federation_endpoint_blocked_total",
            "Requests blocked by Remote mode guard",
            ["path"],
        )


# Global metrics instance
_metrics: FederationMetrics | None = None


def get_metrics() -> FederationMetrics:
    """Get or create global metrics instance."""
    global _metrics
    if _metrics is None:
        _metrics = FederationMetrics()
    return _metrics


# Metric specifications (for documentation/alerting reference)
FEDERATION_METRIC_SPECS = {
    "federation_ws_connected": {
        "type": "gauge",
        "labels": ["remote_id"],
        "help": "Current WebSocket connection state (1=connected, 0=disconnected)",
    },
    "federation_ws_reconnect_total": {
        "type": "counter",
        "labels": ["remote_id", "reason"],
        "help": "WebSocket reconnection count by reason",
    },
    "federation_identity_collision_total": {
        "type": "counter",
        "labels": ["stargate_id"],
        "help": "Duplicate identity connection attempts",
    },
    "federation_auth_failure_total": {
        "type": "counter",
        "labels": ["remote_id", "reason"],
        "help": "Authentication failures by cause",
    },
    "federation_protocol_mismatch_total": {
        "type": "counter",
        "labels": ["local_version", "remote_version"],
        "help": "Protocol version mismatches",
    },
    "federation_telemetry_received_total": {
        "type": "counter",
        "labels": ["remote_id", "signal"],
        "help": "Telemetry events received by type",
    },
    "federation_telemetry_dropped_total": {
        "type": "counter",
        "labels": ["remote_id", "reason"],
        "help": "Telemetry events dropped (backpressure)",
    },
    "federation_telemetry_queue_depth": {
        "type": "gauge",
        "labels": ["remote_id"],
        "help": "Current telemetry queue depth",
    },
    "federation_last_pong_age_ms": {
        "type": "gauge",
        "labels": ["remote_id"],
        "help": "Time since last pong (VPS idle detection)",
    },
    "federation_request_total": {
        "type": "counter",
        "labels": ["remote_id", "status"],
        "help": "Forwarded requests by status",
    },
    "federation_request_latency_ms": {
        "type": "histogram",
        "labels": ["remote_id"],
        "help": "End-to-end request latency",
    },
    "federation_ttft_ms": {
        "type": "histogram",
        "labels": ["remote_id"],
        "help": "Time to first token (streaming)",
    },
    "federation_active_requests": {
        "type": "gauge",
        "labels": ["remote_id"],
        "help": "Currently active requests",
    },
    "federation_cancel_requested_total": {
        "type": "counter",
        "labels": ["remote_id"],
        "help": "Cancel requests issued",
    },
    "federation_cancel_delivered_total": {
        "type": "counter",
        "labels": ["remote_id"],
        "help": "Cancel requests successfully delivered",
    },
    "federation_cancel_failed_total": {
        "type": "counter",
        "labels": ["remote_id", "reason"],
        "help": "Cancel requests that failed",
    },
    "federation_pending_cancels": {
        "type": "gauge",
        "labels": ["remote_id"],
        "help": "Cancels pending reconnect replay",
    },
    "federation_headers_stripped_total": {
        "type": "counter",
        "labels": [],
        "help": "Federation headers stripped at ingress",
    },
    "federation_endpoint_blocked_total": {
        "type": "counter",
        "labels": ["path"],
        "help": "Requests blocked by Remote mode guard",
    },
}

# Required alerts (spec §17)
REQUIRED_ALERTS = [
    {
        "name": "FederationConnectionLost",
        "condition": "federation_ws_connected == 0",
        "for": "5m",
        "severity": "critical",
    },
    {
        "name": "FederationTelemetryStale",
        "condition": "federation_last_pong_age_ms > 30000",
        "for": "1m",
        "severity": "warning",
    },
    {
        "name": "FederationCancelBacklog",
        "condition": "federation_pending_cancels > 100",
        "for": "5m",
        "severity": "warning",
    },
    {
        "name": "FederationRequestErrors",
        "condition": 'rate(federation_request_total{status="error"}[5m]) > 0.1',
        "for": "5m",
        "severity": "warning",
    },
]

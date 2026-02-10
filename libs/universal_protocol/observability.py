"""Observability utilities for debugging and monitoring.

Provides debug statistics for resource usage:
- fds_open: Number of open file descriptors in this process
- tasks_running: Number of concurrent asyncio tasks

Used by debug_stats RPC handler to verify cleanup and detect resource leaks.

Also provides basic metrics collection:
- RPC request counts
- RPC error counts
- Active stream count
- Queue timeout counts
- Backpressure events

Thread Safety: Not needed. All methods called from single-threaded async event loop.
Dict operations (defaultdict) are atomic under GIL.
"""

import asyncio
from universal_logging import get_logger
import os
import time
from collections import defaultdict
from typing import Any

logger = get_logger(__name__)


def get_debug_stats() -> dict[str, Any]:
    """Get current debug statistics including metrics.

    Returns:
        Dict with:
            - fds_open: Number of open file descriptors
            - tasks_running: Number of running asyncio tasks
            - metrics: Protocol metrics (RPC counts, stream counts, etc.)

    Notes:
        - fds_open: Counts from /proc/{pid}/fd (Linux-specific)
        - If /proc not available, returns -1
        - tasks_running: Always available via asyncio.all_tasks()
    """
    # Count open file descriptors (Linux-specific)
    try:
        fds_open = len(os.listdir(f"/proc/{os.getpid()}/fd"))
    except (FileNotFoundError, PermissionError):
        # Fallback: can't count FDs, return -1
        fds_open = -1

    # Count running asyncio tasks
    try:
        tasks_running = len(asyncio.all_tasks())
    except RuntimeError:
        # No running event loop
        tasks_running = 0

    return {
        # Flat structure (backward compatible)
        "fds_open": fds_open,
        "tasks_running": tasks_running,
        # Nested structure (explicit)
        "process": {
            "open_fds": fds_open,
        },
        "tasks": {
            "running": tasks_running,
        },
        "metrics": get_metrics(),  # Include protocol metrics
    }


class ProtocolMetrics:
    """Metrics collection for Universal Protocol.

    Provides basic counters and gauges for monitoring protocol operation.

    Thread Safety: Not needed. Called from async event loop.
    defaultdict operations are atomic under GIL.
    """

    def __init__(self):
        self.rpc_requests_total = defaultdict(int)
        self.rpc_errors_by_code = defaultdict(int)
        self.rpc_errors_by_source = defaultdict(
            lambda: defaultdict(int)
        )  # source -> code -> count
        self.rpc_latency_samples = defaultdict(list)  # Store recent latency samples
        self.streams_active = 0
        self.queue_timeouts_total = 0
        self.backpressure_events = 0

        # New metrics
        self.queue_depth_by_stream = {}  # stream_id -> current depth
        self.stream_durations = []  # List of completed stream durations in seconds
        self.stream_start_times = {}  # stream_id -> start timestamp
        self.token_counts_by_stream = {}  # stream_id -> token count
        self.token_throughput_samples = []  # List of (timestamp, tokens/sec) samples

        # Stream duration histogram buckets (in seconds)
        self.stream_duration_buckets = [
            0.1,
            0.5,
            1.0,
            5.0,
            10.0,
            30.0,
            60.0,
            float("inf"),
        ]
        self.stream_duration_histogram = defaultdict(int)  # bucket -> count

        # Model-aware metrics
        self.stream_model_mapping = {}  # stream_id -> model_name
        self.queue_timeouts_by_model = defaultdict(int)  # model -> timeout count
        self.backpressure_events_by_model = defaultdict(int)  # model -> event count

    def increment_rpc_request(self, method: str):
        """Increment RPC request counter for a method.

        Thread Safety: Not needed. Called from async event loop.
        defaultdict[int] += 1 is atomic under GIL.
        """
        self.rpc_requests_total[method] += 1

    def increment_rpc_error(self, error_code: str, source: str = "rpc"):
        """Increment RPC error counter for an error code and source.

        Args:
            error_code: Error code (e.g., "OOM", "TIMEOUT")
            source: Error source ("rpc", "stream", or "engine")
        """
        self.rpc_errors_by_code[error_code] += 1
        self.rpc_errors_by_source[source][error_code] += 1

    def record_rpc_latency(self, method: str, latency_seconds: float):
        """Record RPC latency for a method (keeps last 100 samples)."""
        samples = self.rpc_latency_samples[method]
        samples.append(latency_seconds)
        # Keep only last 100 samples to prevent memory growth
        if len(samples) > 100:
            samples.pop(0)

    def set_streams_active(self, count: int):
        """Set the number of active streams (gauge)."""
        self.streams_active = count

    def increment_queue_timeout(self, stream_id: str = None):
        """Increment queue timeout counter.

        Args:
            stream_id: Optional stream ID to track model-specific timeouts
        """
        self.queue_timeouts_total += 1

        # Track by model if stream_id provided
        if stream_id and stream_id in self.stream_model_mapping:
            model_name = self.stream_model_mapping[stream_id]
            self.queue_timeouts_by_model[model_name] += 1

    def increment_backpressure_event(self, stream_id: str = None):
        """Increment backpressure event counter.

        Args:
            stream_id: Optional stream ID to track model-specific events
        """
        self.backpressure_events += 1

        # Track by model if stream_id provided
        if stream_id and stream_id in self.stream_model_mapping:
            model_name = self.stream_model_mapping[stream_id]
            self.backpressure_events_by_model[model_name] += 1

    def set_queue_depth(self, stream_id: str, depth: int):
        """Set current queue depth for a stream.

        Args:
            stream_id: Stream identifier
            depth: Current number of items in queue
        """
        if depth > 0:
            self.queue_depth_by_stream[stream_id] = depth
        else:
            # Remove entry when queue is empty
            self.queue_depth_by_stream.pop(stream_id, None)

    def start_stream(self, stream_id: str, model_name: str = None):
        """Mark a stream as started for duration tracking.

        Args:
            stream_id: Stream identifier
            model_name: Optional model name for model-aware metrics
        """
        self.stream_start_times[stream_id] = time.time()
        self.token_counts_by_stream[stream_id] = 0

        # Track model mapping if provided
        if model_name:
            self.stream_model_mapping[stream_id] = model_name

    def end_stream(self, stream_id: str):
        """Mark a stream as ended and record its duration.

        Args:
            stream_id: Stream identifier
        """
        if stream_id in self.stream_start_times:
            duration = time.time() - self.stream_start_times[stream_id]
            self.stream_durations.append(duration)
            # Keep only last 100 durations
            if len(self.stream_durations) > 100:
                self.stream_durations.pop(0)

            # Add to histogram bucket
            for bucket in self.stream_duration_buckets:
                if duration <= bucket:
                    self.stream_duration_histogram[bucket] += 1
                    break

            # Calculate token throughput if tokens were counted
            if stream_id in self.token_counts_by_stream and duration > 0:
                tokens = self.token_counts_by_stream[stream_id]
                throughput = tokens / duration
                self.token_throughput_samples.append((time.time(), throughput))
                # Keep only last 100 samples
                if len(self.token_throughput_samples) > 100:
                    self.token_throughput_samples.pop(0)

            # Clean up stream tracking
            del self.stream_start_times[stream_id]
            self.token_counts_by_stream.pop(stream_id, None)
            self.queue_depth_by_stream.pop(stream_id, None)
            self.stream_model_mapping.pop(stream_id, None)

    def increment_stream_tokens(self, stream_id: str, token_count: int):
        """Increment token count for a stream.

        Args:
            stream_id: Stream identifier
            token_count: Number of tokens to add
        """
        if stream_id in self.token_counts_by_stream:
            self.token_counts_by_stream[stream_id] += token_count

    def get_metrics(self) -> dict[str, Any]:
        """Get current metrics snapshot.

        Returns:
            Dict with all metrics:
            - rpc_requests_total: Dict[method, count]
            - rpc_errors_by_code: Dict[error_code, count]
            - rpc_latency_stats: Dict[method, stats] with avg/min/max/count
            - streams_active: int
            - queue_timeouts_total: int
            - backpressure_events: int
        """
        # Calculate latency statistics
        latency_stats = {}
        for method, samples in self.rpc_latency_samples.items():
            if samples:
                latency_stats[method] = {
                    "avg_seconds": sum(samples) / len(samples),
                    "min_seconds": min(samples),
                    "max_seconds": max(samples),
                    "count": len(samples),
                }

        # Calculate stream duration statistics
        stream_duration_stats = {}
        if self.stream_durations:
            stream_duration_stats = {
                "avg_seconds": sum(self.stream_durations) / len(self.stream_durations),
                "min_seconds": min(self.stream_durations),
                "max_seconds": max(self.stream_durations),
                "count": len(self.stream_durations),
            }

        # Calculate token throughput statistics
        token_throughput_stats = {}
        if self.token_throughput_samples:
            # Get recent samples (last 60 seconds)
            current_time = time.time()
            recent_samples = [
                throughput
                for timestamp, throughput in self.token_throughput_samples
                if current_time - timestamp < 60
            ]
            if recent_samples:
                token_throughput_stats = {
                    "avg_tokens_per_second": sum(recent_samples) / len(recent_samples),
                    "min_tokens_per_second": min(recent_samples),
                    "max_tokens_per_second": max(recent_samples),
                    "count": len(recent_samples),
                }

        # Convert nested error dict to flat format for compatibility
        rpc_errors_by_source_flat = {}
        for source, codes in self.rpc_errors_by_source.items():
            for code, count in codes.items():
                rpc_errors_by_source_flat[f"{source}_{code}"] = count

        # Calculate histogram data for stream durations
        stream_duration_histogram = {}
        cumulative_count = 0
        total_histogram_count = sum(self.stream_duration_histogram.values())

        for bucket in self.stream_duration_buckets:
            count = self.stream_duration_histogram.get(bucket, 0)
            cumulative_count += count
            # Format bucket label (use 'le' for 'less than or equal')
            if bucket == float("inf"):
                bucket_label = "+Inf"
            else:
                bucket_label = str(bucket)
            stream_duration_histogram[bucket_label] = cumulative_count

        return {
            "rpc_requests_total": dict(self.rpc_requests_total),
            "rpc_errors_by_code": dict(self.rpc_errors_by_code),
            "rpc_errors_by_source": dict(self.rpc_errors_by_source),
            "rpc_errors_by_source_flat": rpc_errors_by_source_flat,
            "rpc_latency_stats": latency_stats,
            "streams_active": self.streams_active,
            "queue_timeouts_total": self.queue_timeouts_total,
            "queue_timeouts_by_model": dict(self.queue_timeouts_by_model),
            "backpressure_events": self.backpressure_events,
            "backpressure_events_by_model": dict(self.backpressure_events_by_model),
            "queue_depth_by_stream": dict(self.queue_depth_by_stream),
            "queue_depth_total": sum(self.queue_depth_by_stream.values()),
            "stream_duration_stats": stream_duration_stats,
            "stream_duration_histogram": stream_duration_histogram,
            "stream_duration_histogram_count": total_histogram_count,
            "token_throughput_stats": token_throughput_stats,
        }

    def reset(self):
        """Reset all metrics to zero. Use with caution."""
        self.rpc_requests_total.clear()
        self.rpc_errors_by_code.clear()
        self.rpc_errors_by_source.clear()
        self.rpc_latency_samples.clear()
        self.streams_active = 0
        self.queue_timeouts_total = 0
        self.backpressure_events = 0
        self.queue_depth_by_stream.clear()
        self.stream_durations.clear()
        self.stream_duration_histogram.clear()
        self.stream_start_times.clear()
        self.token_counts_by_stream.clear()
        self.token_throughput_samples.clear()
        self.stream_model_mapping.clear()
        self.queue_timeouts_by_model.clear()
        self.backpressure_events_by_model.clear()


# Global metrics instance
_metrics = ProtocolMetrics()


# Export convenient functions
def increment_rpc_request(method: str):
    """Increment RPC request counter for a method."""
    _metrics.increment_rpc_request(method)


def increment_rpc_error(error_code: str, source: str = "rpc"):
    """Increment RPC error counter for an error code and source."""
    _metrics.increment_rpc_error(error_code, source)


def set_streams_active(count: int):
    """Set the number of active streams."""
    _metrics.set_streams_active(count)


def increment_queue_timeout(stream_id: str = None):
    """Increment queue timeout counter."""
    _metrics.increment_queue_timeout(stream_id)


def increment_backpressure_event(stream_id: str = None):
    """Increment backpressure event counter."""
    _metrics.increment_backpressure_event(stream_id)


def set_queue_depth(stream_id: str, depth: int):
    """Set current queue depth for a stream."""
    _metrics.set_queue_depth(stream_id, depth)


def start_stream(stream_id: str, model_name: str = None):
    """Mark a stream as started for duration tracking."""
    _metrics.start_stream(stream_id, model_name)


def end_stream(stream_id: str):
    """Mark a stream as ended and record its duration."""
    _metrics.end_stream(stream_id)


def increment_stream_tokens(stream_id: str, token_count: int):
    """Increment token count for a stream."""
    _metrics.increment_stream_tokens(stream_id, token_count)


def get_metrics() -> dict[str, Any]:
    """Get current metrics snapshot."""
    return _metrics.get_metrics()


def get_metrics_instance() -> ProtocolMetrics:
    """Get the global metrics instance for direct access."""
    return _metrics


def reset_metrics():
    """Reset all metrics. Use with caution."""
    _metrics.reset()

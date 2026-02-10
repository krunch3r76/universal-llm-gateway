"""
Federation orchestration metrics (Phase 4.2: metrics-only).

INVARIANT: metrics do not influence routing/forwarding decisions (read-only visibility)
INVARIANT: ∀ record_*: O(1)
INVARIANT: get_summary() may be O(N log N) due to percentile computation
INVARIANT: Memory bounded by deque(maxlen=1000)
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True, kw_only=True)
class OrchestrationMetrics:
    """
    In-memory metrics for federation load orchestration.

    Callers vs operations:
    - callers_total increments for every ensure_model_loaded_on_remote()
      invocation
    - operations_total increments only for the primary caller that performs
      HTTP /models/load
    """

    load_callers_total: int = 0
    load_operations_total: int = 0
    load_operations_success_total: int = 0
    load_operations_failure_total: int = 0

    # Single-flight
    coalesced_callers_total: int = 0
    primary_callers_total: int = 0

    # Telemetry quality
    stale_telemetry_events_total: int = 0

    # Reserved for Phase 4.3+ (kept for continuity; not wired in Phase 4.2)
    telemetry_skip_attempted_total: int = 0
    telemetry_skip_success_total: int = 0
    telemetry_skip_failed_total: int = 0
    split_brain_recoveries_total: int = 0
    retry_attempts_total: int = 0
    retries_exhausted_total: int = 0

    _load_latencies_ms: deque[float] = field(default_factory=lambda: deque(maxlen=1000))
    _start_time: float = field(default_factory=time.time)

    def record_primary_caller(self) -> None:
        self.load_callers_total += 1
        self.primary_callers_total += 1

    def record_coalesced_caller(self) -> None:
        self.load_callers_total += 1
        self.coalesced_callers_total += 1

    def record_load_operation_success(self, duration_seconds: float) -> None:
        self.load_operations_total += 1
        self.load_operations_success_total += 1
        self._load_latencies_ms.append(duration_seconds * 1000)

    def record_load_operation_failure(self, duration_seconds: float) -> None:
        self.load_operations_total += 1
        self.load_operations_failure_total += 1
        self._load_latencies_ms.append(duration_seconds * 1000)

    def record_stale_telemetry(self) -> None:
        self.stale_telemetry_events_total += 1

    def record_retry(self) -> None:
        self.retry_attempts_total += 1

    def record_retries_exhausted(self) -> None:
        self.retries_exhausted_total += 1

    def record_telemetry_skip(self) -> None:
        """Record when load was skipped based on telemetry hint."""
        self.telemetry_skip_attempted_total += 1
        self.telemetry_skip_success_total += 1

    def get_summary(self) -> dict[str, Any]:
        uptime = time.time() - self._start_time
        latencies = list(self._load_latencies_ms)

        if latencies:
            sorted_lat = sorted(latencies)
            n = len(sorted_lat)
            avg_latency = sum(sorted_lat) / n
            # NOTE: For even n, this is the upper median
            # (element just above true median)
            # Acceptable approximation for monitoring purposes
            p50_latency = sorted_lat[n // 2]
            p99_latency = sorted_lat[min(int(n * 0.99), n - 1)]
        else:
            avg_latency = p50_latency = p99_latency = 0.0

        op_success_rate = (
            self.load_operations_success_total / self.load_operations_total * 100
            if self.load_operations_total > 0
            else 0.0
        )
        coalesce_rate = (
            self.coalesced_callers_total / self.load_callers_total * 100
            if self.load_callers_total > 0
            else 0.0
        )

        return {
            "uptime_seconds": round(uptime, 1),
            "load_callers_total": self.load_callers_total,
            "primary_callers_total": self.primary_callers_total,
            "coalesced_callers_total": self.coalesced_callers_total,
            "coalesce_rate_percent": round(coalesce_rate, 2),
            "load_operations_total": self.load_operations_total,
            "load_operations_success_total": self.load_operations_success_total,
            "load_operations_failure_total": self.load_operations_failure_total,
            "load_operation_success_rate_percent": round(op_success_rate, 2),
            "avg_load_latency_ms": round(avg_latency, 2),
            "p50_load_latency_ms": round(p50_latency, 2),
            "p99_load_latency_ms": round(p99_latency, 2),
            "latency_sample_count": len(latencies),
            "stale_telemetry_events_total": self.stale_telemetry_events_total,
            # Reserved for Phase 4.3+
            "telemetry_skip_attempted_total": self.telemetry_skip_attempted_total,
            "telemetry_skip_success_total": self.telemetry_skip_success_total,
            "telemetry_skip_failed_total": self.telemetry_skip_failed_total,
            "split_brain_recoveries_total": self.split_brain_recoveries_total,
            "retry_attempts_total": self.retry_attempts_total,
            "retries_exhausted_total": self.retries_exhausted_total,
        }


def create_metrics_endpoint(metrics: OrchestrationMetrics):
    """
    Create FastAPI router for orchestration metrics.

    NOTE: Auth is applied at include_router(...) time (proxy startup),
    not inside this module.
    """
    from fastapi import APIRouter

    router = APIRouter(
        prefix="/api/v1/federation/orchestration",
        tags=["federation-metrics"],
    )

    @router.get("/metrics")
    async def get_orchestration_metrics() -> dict[str, Any]:
        return metrics.get_summary()

    return router

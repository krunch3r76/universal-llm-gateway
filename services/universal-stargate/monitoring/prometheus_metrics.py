"""
Prometheus metrics exporter for atomic VRAM reservation system.

Exposes metrics for monitoring reservation state, performance, and resource usage.
"""

import time
from dataclasses import dataclass

from universal_logging import get_logger

logger = get_logger(__name__)


@dataclass
class ReservationMetrics:
    """Metrics snapshot for reservations."""

    active_reservations: int
    pending_reservations: int
    completed_reservations: int
    failed_reservations: int
    expired_reservations: int
    total_vram_reserved_mb: int
    total_ram_reserved_mb: int
    avg_reservation_duration_ms: float
    p95_reservation_duration_ms: float
    p99_reservation_duration_ms: float


class PrometheusMetricsExporter:
    """
    Exports atomic reservation metrics in Prometheus format.

    Provides /metrics endpoint compatible with Prometheus scraping.
    """

    def __init__(self):
        self._gateway_metrics: dict[str, dict] = {}
        self._last_scrape_time = 0

    def update_gateway_metrics(self, gateway_id: str, resource_manager):
        """
        Update metrics for a specific gateway.

        Args:
            gateway_id: Gateway identifier
            resource_manager: GatewayResourceManager instance
        """
        try:
            res_metrics = resource_manager.get_metrics() if resource_manager else {}

            self._gateway_metrics[gateway_id] = {
                "resource_manager": res_metrics,
                "timestamp": time.time(),
            }
        except Exception as e:
            logger.error(f"Error updating metrics for gateway {gateway_id}: {e}")

    def generate_prometheus_text(self) -> str:
        """
        Generate Prometheus metrics in text exposition format.

        Returns:
            Prometheus-formatted metrics text
        """
        self._last_scrape_time = time.time()
        lines = []

        # Add header comments
        lines.append(
            "# HELP gateway_reservation_active Active VRAM reservations per gateway"
        )
        lines.append("# TYPE gateway_reservation_active gauge")

        lines.append(
            "# HELP gateway_reservation_pending Pending VRAM reservations per gateway"
        )
        lines.append("# TYPE gateway_reservation_pending gauge")

        lines.append(
            "# HELP gateway_reservation_total Total VRAM reservations by state"
        )
        lines.append("# TYPE gateway_reservation_total counter")

        lines.append("# HELP gateway_vram_reserved_mb Total VRAM reserved in MB")
        lines.append("# TYPE gateway_vram_reserved_mb gauge")

        lines.append("# HELP gateway_ram_reserved_mb Total RAM reserved in MB")
        lines.append("# TYPE gateway_ram_reserved_mb gauge")

        # Generate metrics for each gateway
        for gateway_id, metrics in self._gateway_metrics.items():
            res_metrics = metrics.get("resource_manager", {})

            # Reservation metrics
            reservations = res_metrics.get("reservations", {})
            lines.append(
                f'gateway_reservation_active{{gateway="{gateway_id}"}} {reservations.get("active", 0)}'
            )
            lines.append(
                f'gateway_reservation_pending{{gateway="{gateway_id}"}} {reservations.get("pending", 0)}'
            )
            lines.append(
                f'gateway_reservation_total{{gateway="{gateway_id}",state="completed"}} {reservations.get("completed", 0)}'
            )
            lines.append(
                f'gateway_reservation_total{{gateway="{gateway_id}",state="failed"}} {reservations.get("failed", 0)}'
            )
            lines.append(
                f'gateway_reservation_total{{gateway="{gateway_id}",state="expired"}} {reservations.get("expired", 0)}'
            )

            # Resource metrics
            resources = res_metrics.get("resources", {})
            lines.append(
                f'gateway_vram_reserved_mb{{gateway="{gateway_id}"}} {resources.get("vram_reserved_mb", 0)}'
            )
            lines.append(
                f'gateway_ram_reserved_mb{{gateway="{gateway_id}"}} {resources.get("ram_reserved_mb", 0)}'
            )

        return "\n".join(lines) + "\n"

    def get_metrics_summary(self) -> dict:
        """
        Get human-readable metrics summary for debugging.

        Returns:
            Dictionary with aggregated metrics
        """
        summary = {
            "total_gateways": len(self._gateway_metrics),
            "gateways": {},
            "aggregated": {
                "active_reservations": 0,
                "total_vram_reserved_mb": 0,
                "total_ram_reserved_mb": 0,
            },
        }

        for gateway_id, metrics in self._gateway_metrics.items():
            res_metrics = metrics.get("resource_manager", {})

            reservations = res_metrics.get("reservations", {})
            resources = res_metrics.get("resources", {})

            summary["gateways"][gateway_id] = {
                "active_reservations": reservations.get("active", 0),
                "vram_reserved_mb": resources.get("vram_reserved_mb", 0),
                "ram_reserved_mb": resources.get("ram_reserved_mb", 0),
            }

            # Aggregate
            summary["aggregated"]["active_reservations"] += reservations.get(
                "active", 0
            )
            summary["aggregated"]["total_vram_reserved_mb"] += resources.get(
                "vram_reserved_mb", 0
            )
            summary["aggregated"]["total_ram_reserved_mb"] += resources.get(
                "ram_reserved_mb", 0
            )

        return summary


# Global exporter instance
_exporter: PrometheusMetricsExporter | None = None


def get_metrics_exporter() -> PrometheusMetricsExporter:
    """Get or create global metrics exporter instance."""
    global _exporter
    if _exporter is None:
        _exporter = PrometheusMetricsExporter()
    return _exporter

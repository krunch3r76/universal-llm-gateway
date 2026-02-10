"""
Real-time gateway state monitoring dashboard.

Provides comprehensive visualization of gateway states, transitions,
and operational metrics for monitoring and alerting.
"""

from datetime import datetime, timedelta

from universal_logging import get_logger

logger = get_logger(__name__)


class GatewayStateDashboard:
    """
    Real-time dashboard for gateway state monitoring.

    Provides:
    - Current state visualization for all gateways
    - State transition timeline
    - Alert integration for extended downtimes
    - Performance impact analysis
    """

    def __init__(
        self, monitoring_consumer, metrics_consumer, alert_threshold_minutes: int = 5
    ):
        """
        Initialize gateway state dashboard.

        Args:
            monitoring_consumer: MonitoringConsumer instance for state tracking
            metrics_consumer: MetricsConsumer instance for analytics
            alert_threshold_minutes: Threshold for downtime alerts (minutes)
        """
        self.monitoring_consumer = monitoring_consumer
        self.metrics_consumer = metrics_consumer
        self.alert_threshold_seconds = alert_threshold_minutes * 60
        self._active_alerts: dict[str, dict] = {}

    def get_dashboard_data(self) -> dict:
        """
        Get comprehensive dashboard data.

        Returns:
            Dictionary with all dashboard information
        """
        # Get monitoring data
        monitoring_summary = self.monitoring_consumer.get_dashboard_summary()

        # Get metrics data
        gateway_metrics = self.metrics_consumer.get_all_gateway_metrics()
        system_metrics = self.metrics_consumer.get_system_wide_metrics()

        # Check for alerts
        alerts = self._check_for_alerts()

        return {
            "timestamp": datetime.now().isoformat(),
            "monitoring": monitoring_summary,
            "metrics": {"gateways": gateway_metrics, "system": system_metrics},
            "alerts": alerts,
        }

    def get_gateway_detail(self, url: str) -> dict:
        """
        Get detailed information for a specific gateway.

        Args:
            url: Gateway URL

        Returns:
            Dictionary with gateway details
        """
        # Get current state
        current_states = self.monitoring_consumer.get_current_states()
        current_state = current_states.get(url)

        # Get uptime stats
        uptime_stats = self.monitoring_consumer.get_uptime_stats(url)

        # Get metrics
        gateway_metrics = self.metrics_consumer.get_comprehensive_metrics(url)

        # Get recent transitions
        all_transitions = self.monitoring_consumer.get_state_history(limit=100)
        gateway_transitions = [t for t in all_transitions if t["url"] == url][
            :10
        ]  # Last 10 for this gateway

        return {
            "url": url,
            "current_state": current_state,
            "uptime_stats": uptime_stats,
            "metrics": gateway_metrics,
            "recent_transitions": gateway_transitions,
        }

    def get_state_timeline(self, hours: int = 24) -> list[dict]:
        """
        Get state transition timeline for visualization.

        Args:
            hours: Number of hours to include in timeline

        Returns:
            List of transition events for timeline display
        """
        cutoff_time = datetime.now() - timedelta(hours=hours)
        all_transitions = self.monitoring_consumer.get_state_history()

        # Filter to requested time window
        timeline = [
            t
            for t in all_transitions
            if datetime.fromisoformat(t["datetime"]) > cutoff_time
        ]

        return timeline

    def get_performance_analysis(self) -> dict:
        """
        Get performance impact analysis.

        Returns:
            Dictionary with performance analysis data
        """
        system_metrics = self.metrics_consumer.get_system_wide_metrics()

        # Analyze time patterns
        time_patterns = system_metrics["time_patterns"]
        peak_hour = time_patterns.get("peak_hour")

        # Analyze gateway reliability
        gateway_metrics = self.metrics_consumer.get_all_gateway_metrics()
        reliability_scores = {
            url: metrics["reliability_score"]
            for url, metrics in gateway_metrics.items()
        }

        most_reliable = (
            max(reliability_scores.items(), key=lambda x: x[1])
            if reliability_scores
            else (None, 0)
        )
        least_reliable = (
            min(reliability_scores.items(), key=lambda x: x[1])
            if reliability_scores
            else (None, 0)
        )

        return {
            "system_metrics": system_metrics,
            "time_patterns": time_patterns,
            "peak_hour": peak_hour,
            "reliability": {
                "most_reliable_gateway": most_reliable[0],
                "most_reliable_score": most_reliable[1],
                "least_reliable_gateway": least_reliable[0],
                "least_reliable_score": least_reliable[1],
                "average_reliability": system_metrics["average_reliability"],
            },
        }

    def _check_for_alerts(self) -> list[dict]:
        """
        Check for alert conditions and generate alerts.

        Returns:
            List of active alerts
        """
        alerts = []
        current_states = self.monitoring_consumer.get_current_states()

        for url, state in current_states.items():
            # Check for extended downtime
            is_available = (
                state["connectivity"] == "reachable" and state["health"] == "healthy"
            )

            if not is_available:
                uptime_stats = self.monitoring_consumer.get_uptime_stats(url)
                if not uptime_stats["currently_up"]:
                    # Gateway is down - check duration
                    self.metrics_consumer.get_downtime_statistics(url)

                    # Create alert if not already active
                    if url not in self._active_alerts:
                        alert = {
                            "type": "extended_downtime",
                            "gateway_url": url,
                            "severity": "warning",
                            "message": f"Gateway {url} is unavailable",
                            "connectivity": state["connectivity"],
                            "health": state["health"],
                            "started_at": datetime.now().isoformat(),
                        }
                        self._active_alerts[url] = alert
                        logger.warning(f"🚨 Alert: {alert['message']}")
                        alerts.append(alert)
                    else:
                        # Update existing alert
                        alerts.append(self._active_alerts[url])
            else:
                # Gateway is available - clear alert if exists
                if url in self._active_alerts:
                    logger.info(f"✅ Alert cleared: Gateway {url} is now available")
                    del self._active_alerts[url]

        return alerts

    def export_prometheus_metrics(self) -> str:
        """
        Export dashboard metrics in Prometheus format.

        Returns:
            Prometheus-formatted metrics string
        """
        # Get base metrics from monitoring consumer
        base_metrics = self.monitoring_consumer.get_prometheus_metrics()

        # Add alert metrics
        alert_metrics = [
            "# HELP gateway_active_alerts Number of active alerts",
            "# TYPE gateway_active_alerts gauge",
            f"gateway_active_alerts {len(self._active_alerts)}",
        ]

        return base_metrics + "\n" + "\n".join(alert_metrics)

    def get_api_summary(self) -> dict:
        """
        Get summary data for REST API responses.

        Returns:
            Dictionary with API-friendly summary data
        """
        dashboard_data = self.get_dashboard_data()

        return {
            "status": "ok",
            "timestamp": dashboard_data["timestamp"],
            "summary": {
                "total_gateways": dashboard_data["monitoring"]["total_gateways"],
                "available_gateways": dashboard_data["monitoring"][
                    "available_gateways"
                ],
                "active_alerts": len(dashboard_data["alerts"]),
            },
            "gateways": dashboard_data["monitoring"]["current_states"],
            "alerts": dashboard_data["alerts"],
        }

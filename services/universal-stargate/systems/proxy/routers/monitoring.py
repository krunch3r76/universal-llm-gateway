"""
Gateway state monitoring API endpoints.

Provides REST API access to gateway state information, metrics,
and monitoring dashboard data.
"""

from fastapi import APIRouter, Depends, HTTPException
from universal_logging import get_logger

from ..dependencies import get_proxy
from ..stargate_core import StargateProxy

logger = get_logger(__name__)
router = APIRouter(tags=["monitoring"], prefix="/api/v1/monitoring")


@router.get("/gateway-states")
async def get_gateway_states(proxy: StargateProxy = Depends(get_proxy)):
    """
    Get current states for all gateways.

    Returns:
        Dictionary mapping gateway URLs to their current states
    """
    if not hasattr(proxy, "monitoring_consumer") or not proxy.monitoring_consumer:
        raise HTTPException(status_code=503, detail="Monitoring consumer not available")

    states = proxy.monitoring_consumer.get_current_states()
    return {"status": "ok", "gateways": states}


@router.get("/gateway-states/{gateway_url:path}")
async def get_gateway_state_detail(
    gateway_url: str, proxy: StargateProxy = Depends(get_proxy)
):
    """
    Get detailed state information for a specific gateway.

    Args:
        gateway_url: Gateway URL (URL-encoded)

    Returns:
        Detailed gateway state information
    """
    if not hasattr(proxy, "dashboard") or not proxy.dashboard:
        raise HTTPException(status_code=503, detail="Dashboard not available")

    try:
        detail = proxy.dashboard.get_gateway_detail(gateway_url)
        return {"status": "ok", "gateway": detail}
    except Exception as e:
        logger.error(f"Error getting gateway detail: {e}")
        raise HTTPException(
            status_code=500, detail=f"Error retrieving gateway detail: {str(e)}"
        )


@router.get("/state-history")
async def get_state_history(
    limit: int | None = 100, proxy: StargateProxy = Depends(get_proxy)
):
    """
    Get historical state transitions.

    Args:
        limit: Maximum number of transitions to return (default: 100)

    Returns:
        List of state transitions (most recent first)
    """
    if not hasattr(proxy, "monitoring_consumer") or not proxy.monitoring_consumer:
        raise HTTPException(status_code=503, detail="Monitoring consumer not available")

    history = proxy.monitoring_consumer.get_state_history(limit=limit)
    return {"status": "ok", "transitions": history, "count": len(history)}


@router.get("/timeline")
async def get_state_timeline(
    hours: int = 24, proxy: StargateProxy = Depends(get_proxy)
):
    """
    Get state transition timeline for visualization.

    Args:
        hours: Number of hours to include in timeline (default: 24)

    Returns:
        Timeline of state transitions
    """
    if not hasattr(proxy, "dashboard") or not proxy.dashboard:
        raise HTTPException(status_code=503, detail="Dashboard not available")

    timeline = proxy.dashboard.get_state_timeline(hours=hours)
    return {
        "status": "ok",
        "timeline": timeline,
        "hours": hours,
        "count": len(timeline),
    }


@router.get("/metrics/gateway/{gateway_url:path}")
async def get_gateway_metrics(
    gateway_url: str, proxy: StargateProxy = Depends(get_proxy)
):
    """
    Get comprehensive metrics for a specific gateway.

    Args:
        gateway_url: Gateway URL (URL-encoded)

    Returns:
        Gateway metrics including transitions, uptime, downtime, and performance
    """
    if not hasattr(proxy, "metrics_consumer") or not proxy.metrics_consumer:
        raise HTTPException(status_code=503, detail="Metrics consumer not available")

    metrics = proxy.metrics_consumer.get_comprehensive_metrics(gateway_url)
    return {"status": "ok", "gateway_url": gateway_url, "metrics": metrics}


@router.get("/metrics/system")
async def get_system_metrics(proxy: StargateProxy = Depends(get_proxy)):
    """
    Get system-wide metrics across all gateways.

    Returns:
        Aggregated system metrics
    """
    if not hasattr(proxy, "metrics_consumer") or not proxy.metrics_consumer:
        raise HTTPException(status_code=503, detail="Metrics consumer not available")

    metrics = proxy.metrics_consumer.get_system_wide_metrics()
    return {"status": "ok", "metrics": metrics}


@router.get("/metrics/prometheus")
async def get_prometheus_metrics(proxy: StargateProxy = Depends(get_proxy)):
    """
    Export metrics in Prometheus format.

    Returns:
        Prometheus-formatted metrics string
    """
    if not hasattr(proxy, "dashboard") or not proxy.dashboard:
        raise HTTPException(status_code=503, detail="Dashboard not available")

    metrics_text = proxy.dashboard.export_prometheus_metrics()
    return metrics_text


@router.get("/dashboard")
async def get_dashboard_summary(proxy: StargateProxy = Depends(get_proxy)):
    """
    Get comprehensive dashboard summary.

    Returns:
        Complete dashboard data including states, metrics, and alerts
    """
    if not hasattr(proxy, "dashboard") or not proxy.dashboard:
        raise HTTPException(status_code=503, detail="Dashboard not available")

    dashboard_data = proxy.dashboard.get_api_summary()
    return dashboard_data


@router.get("/routing/available-gateways")
async def get_available_gateways(proxy: StargateProxy = Depends(get_proxy)):
    """
    Get list of currently available gateways for routing.

    Returns:
        List of gateway URLs that are available for request routing
    """
    if not hasattr(proxy, "routing_consumer") or not proxy.routing_consumer:
        raise HTTPException(status_code=503, detail="Routing consumer not available")

    available = proxy.routing_consumer.get_available_gateways()
    stats = proxy.routing_consumer.get_routing_statistics()

    return {
        "status": "ok",
        "available_gateways": available,
        "routing_statistics": stats,
    }


@router.get("/performance-analysis")
async def get_performance_analysis(proxy: StargateProxy = Depends(get_proxy)):
    """
    Get performance impact analysis.

    Returns:
        Performance analysis data including patterns and reliability
    """
    if not hasattr(proxy, "dashboard") or not proxy.dashboard:
        raise HTTPException(status_code=503, detail="Dashboard not available")

    analysis = proxy.dashboard.get_performance_analysis()
    return {"status": "ok", "analysis": analysis}

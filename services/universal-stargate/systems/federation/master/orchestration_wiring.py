"""
Orchestration component wiring for Master mode.

Creates and connects the forwarder, cancel sender, request tracker,
load orchestrator, and metrics endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import Depends, FastAPI
from universal_logging import get_logger

from .routing.cancel_sender import FederationCancelSender
from .routing.forward import FederatedRequestForwarder

if TYPE_CHECKING:
    from ..common.config import FederationConfig
    from ..common.connection_manager import ConnectionManager
    from .manager.federated_gateway_manager import FederatedGatewayManager
    from .orchestration.load_orchestrator import FederatedLoadOrchestrator
    from .orchestration.metrics import OrchestrationMetrics
    from .routing.orchestrator import MasterRequestTracker

logger = get_logger(__name__)


@dataclass(slots=True, kw_only=True)
class OrchestrationComponents:
    """Container for all orchestration components created during wiring."""

    forwarder: FederatedRequestForwarder
    load_orchestrator: FederatedLoadOrchestrator
    metrics: OrchestrationMetrics
    request_tracker: MasterRequestTracker


def wire_orchestration(
    *,
    app: FastAPI,
    config: FederationConfig,
    federated_manager: FederatedGatewayManager,
    connection_manager: ConnectionManager | None,
    event_bus: object | None,
) -> OrchestrationComponents:
    """
    Create and connect all orchestration components for Master mode.

    Args:
        app: FastAPI application for metrics endpoint registration
        config: Federation configuration
        federated_manager: Gateway manager for routing decisions
        connection_manager: Connection manager for WS cancel path
        event_bus: Event bus for orchestration events

    Returns:
        OrchestrationComponents with all wired components
    """
    forwarder = FederatedRequestForwarder(config, event_bus=event_bus)

    from .orchestration import (
        FederatedLoadOrchestrator,
        OrchestrationConfig,
        OrchestrationMetrics,
        create_metrics_endpoint,
    )
    from .routing.orchestrator import MasterRequestTracker

    orch_schema = config.orchestration
    orch_config = OrchestrationConfig(
        load_timeout=orch_schema.load_timeout,
        coalesce_wait_timeout=orch_schema.coalesce_wait_timeout,
        telemetry_staleness_threshold=orch_schema.telemetry_staleness_threshold,
        load_retry_count=orch_schema.load_retry_count,
        load_retry_delay=orch_schema.load_retry_delay,
        load_retry_backoff=orch_schema.load_retry_backoff,
        load_retry_max_delay=orch_schema.load_retry_max_delay,
        load_retry_jitter=orch_schema.load_retry_jitter,
    )

    metrics = OrchestrationMetrics()

    cancel_sender = FederationCancelSender(
        config=config,
        connection_manager=connection_manager,
    )

    async def send_cancel(remote_id: str, request_id: str) -> bool:
        return await cancel_sender.send_cancel(remote_id, request_id)

    request_tracker = MasterRequestTracker(
        forwarder=forwarder,
        send_cancel=send_cancel,
    )

    load_orchestrator = FederatedLoadOrchestrator(
        forwarder=forwarder,
        config=orch_config,
        gateway_manager=federated_manager,
        metrics=metrics,
        event_bus=event_bus,
        request_tracker=request_tracker,
    )

    # Mount metrics endpoint with auth protection (internal-only)
    from systems.proxy.dependencies import get_auth_dependency

    metrics_router = create_metrics_endpoint(metrics)
    app.include_router(
        metrics_router,
        dependencies=[Depends(get_auth_dependency)],
    )
    logger.info("Orchestration metrics endpoint mounted (internal-only)")

    return OrchestrationComponents(
        forwarder=forwarder,
        load_orchestrator=load_orchestrator,
        metrics=metrics,
        request_tracker=request_tracker,
    )

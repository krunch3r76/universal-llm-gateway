"""Fleet decision observability — events + grep-friendly logs (restart-storm AC)."""

from __future__ import annotations

import contextlib
import logging

from scripts.model_manager.observation_event import (
    emit_fleet_cdp_ask_restart_decision,
    emit_fleet_operation_completed,
    emit_fleet_operation_rejected,
)

logger = logging.getLogger(__name__)

CdpAskDecisionReason = str
CdpAskPhase = str
CdpAskAction = str


async def record_fleet_operation_rejected(
    *,
    operation: str,
    running_operation: str,
    caller: str | None,
    reason: str | None,
) -> None:
    logger.info(
        "fleet.operation.rejected operation=%s running_operation=%s caller=%s reason=%s",
        operation,
        running_operation,
        caller,
        reason,
    )
    with contextlib.suppress(Exception):
        await emit_fleet_operation_rejected(
            operation=operation,
            running_operation=running_operation,
            caller=caller,
            reason=reason,
        )


async def record_fleet_operation_completed(
    *,
    operation: str,
    build: bool,
    success: bool,
    duration_s: float,
    failures: list[str],
    caller: str | None,
    reason: str | None,
) -> None:
    logger.info(
        "fleet.operation.completed operation=%s success=%s caller=%s reason=%s failures=%s duration_s=%s",
        operation,
        success,
        caller,
        reason,
        failures,
        round(duration_s, 3),
    )
    with contextlib.suppress(Exception):
        await emit_fleet_operation_completed(
            operation=operation,
            build=build,
            success=success,
            duration_s=duration_s,
            failures=failures,
            caller=caller,
            reason=reason,
        )


async def record_fleet_cdp_ask_restart_decision(
    *,
    phase: CdpAskPhase,
    action: CdpAskAction,
    reason: CdpAskDecisionReason,
    hostname: str | None = None,
) -> None:
    logger.info(
        "fleet.cdp_ask.restart_decision phase=%s action=%s reason=%s hostname=%s",
        phase,
        action,
        reason,
        hostname,
    )
    with contextlib.suppress(Exception):
        await emit_fleet_cdp_ask_restart_decision(
            phase=phase,
            action=action,
            reason=reason,
            hostname=hostname,
        )

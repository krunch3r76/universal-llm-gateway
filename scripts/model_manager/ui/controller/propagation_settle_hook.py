"""Post-restart propagation ledger settle — shared by drain supervisor and lifecycle wrapper."""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from scripts.model_manager.observation_event import (
    emit_manage_propagation_settle_looked_empty,
)

logger = logging.getLogger(__name__)

SettleSource = Literal["drain", "lifecycle_wrapper"]


async def invoke_propagation_settle_for_service(
    service: str,
    *,
    settle_not_before_monotonic: float,
    source: SettleSource,
) -> None:
    """Close or fail open propagation rows from observed liveness after restart."""
    try:
        from charter_runner_store.propagation_terminal import (
            default_probe,
            settle_open_rows_for_service,
        )

        results = await asyncio.to_thread(
            settle_open_rows_for_service,
            service,
            default_probe,
            defer_if_unreachable=True,
            settle_not_before_monotonic=settle_not_before_monotonic,
        )
        if not results:
            await emit_manage_propagation_settle_looked_empty(
                service=service,
                settle_not_before_monotonic=settle_not_before_monotonic,
                source=source,
            )
        for item in results:
            logger.info(
                "propagation ledger settle service=%s row=%s outcome=%s detail=%s",
                service,
                item.row_id,
                item.outcome,
                item.detail,
            )
    except Exception:  # noqa: BLE001 — ledger settle must not fail the restart
        logger.exception(
            "propagation ledger settle failed after restart complete service=%s",
            service,
        )


__all__ = ["SettleSource", "invoke_propagation_settle_for_service"]

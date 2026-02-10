"""
Client disconnection monitoring for pipeline execution.

Detects when HTTP client disconnects during long-running pipeline execution
and cancels the pipeline to avoid wasted computation.

Framework Limitation:
    FastAPI/Starlette does not provide event-based disconnection detection.
    We poll request.is_disconnected() at intervals (same pattern as
    proxy/core/nonstreaming/forwarder.py::_monitor_client_disconnection).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from fastapi import Request

    from .executor import DAGExecutor

logger = get_logger(__name__)


async def execute_with_disconnect_monitoring(
    dag_executor: DAGExecutor,
    http_request: Request,
    pipeline_id: str,
    execution_id: str,
    step_count: int,
) -> None:
    """
    Execute DAG with client disconnection monitoring.

    Races DAG execution against a disconnection monitor task.
    If client disconnects, cancels DAG execution and raises CancelledError.

    Args:
        dag_executor: The DAGExecutor instance
        http_request: FastAPI Request for disconnection detection
        pipeline_id: Pipeline ID for logging
        execution_id: Execution ID for logging
        step_count: Number of steps (for adaptive interval)

    Raises:
        asyncio.CancelledError: When client disconnects
        Exception: Any exception from DAG execution
    """

    async def monitor_disconnection() -> None:
        """Poll for client disconnection."""
        # Adaptive interval based on pipeline complexity
        # More steps = longer expected duration = less frequent checks
        check_interval = 2.0 if step_count > 5 else 1.0
        initial_grace_period = 2.0

        await asyncio.sleep(initial_grace_period)

        while True:
            await asyncio.sleep(check_interval)
            try:
                if await http_request.is_disconnected():
                    logger.info(
                        f"🔌 Client disconnected during pipeline "
                        f"'{pipeline_id}' execution "
                        f"(execution_id={execution_id})"
                    )
                    return  # Signal disconnection by returning
            except Exception as e:
                # Log but continue monitoring
                logger.debug(f"Error checking disconnection: {e}")

    # Create tasks for execution and monitoring
    execution_task = asyncio.create_task(dag_executor.execute(), name="dag-execution")
    monitor_task = asyncio.create_task(
        monitor_disconnection(), name="disconnect-monitor"
    )

    try:
        # Wait for either execution to complete or disconnection
        done, _ = await asyncio.wait(
            [execution_task, monitor_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Check which task completed
        if execution_task in done:
            # Normal completion - cancel monitor and propagate any exception
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass

            # Re-raise any exception from execution
            execution_task.result()

        else:
            # Monitor completed first = client disconnected
            # Cancel the execution task
            execution_task.cancel()
            try:
                await execution_task
            except asyncio.CancelledError:
                pass

            # Raise CancelledError to signal disconnection
            raise asyncio.CancelledError("Client disconnected")

    except asyncio.CancelledError:
        # Ensure both tasks are cancelled
        for task in [execution_task, monitor_task]:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        raise

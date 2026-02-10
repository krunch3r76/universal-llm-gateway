"""Health monitoring lifecycle management for crashed processes."""

from universal_logging import get_logger

from ..state import ProcessState

logger = get_logger(__name__)


async def stop_health_monitoring(process_id: str, state: ProcessState) -> None:
    """
    Stop health monitoring for crashed worker to prevent duplicate reports.
    
    Error isolated - failures logged but don't propagate.
    
    Args:
        process_id: Process/model ID that crashed
        state: Process state container
    """
    try:
        supervisor = state.get_supervisor(process_id)
        if supervisor and hasattr(supervisor, "_health_monitor"):
            try:
                await supervisor._health_monitor.stop_monitoring()
                logger.info(
                    f"🛑 Stopped health monitoring for crashed worker {process_id}"
                )
            except Exception as monitor_err:
                logger.warning(
                    f"Failed to stop health monitor for {process_id}: {monitor_err}"
                )
    except Exception as e:
        logger.error(f"Failed to access supervisor for {process_id}: {e}")

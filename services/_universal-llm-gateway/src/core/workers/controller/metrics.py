"""Active-model tracking, metrics, and orphan cleanup mixin."""

from typing import Any

from process_ipc import ProcessStatus
from process_ipc.core.exceptions import ProcessError

from ._runtime import (
    _emit_embedding_debug,
    _get_resource_tracker,
    logger,
    structured_logger,
)


class MetricsMixin:
    """Active model queries, worker metrics, and orphaned process cleanup."""

    def get_active_model_id(self) -> str | None:
        try:
            loaded = [
                mid
                for mid, info in self.get_all_process_info().items()
                if info.get("status") == ProcessStatus.RUNNING.value
            ]
            return loaded[0] if loaded else None
        except Exception:
            return None

    def get_active_models(self) -> list[str]:
        return _get_resource_tracker().get_loaded_models()

    async def is_model_loaded(self, model_id: str) -> bool:
        try:
            process_status = await self.get_process_status(model_id)
            if not process_status:
                await _emit_embedding_debug(
                    "load_gate_process_status_missing",
                    model_id,
                    None,
                )
                return False
            alive = await self.is_process_alive(model_id)
            structured_logger.info(
                f"{model_id}:liveness_check: {'SUCCESS' if alive else 'FAILED'}"
            )
            await _emit_embedding_debug(
                "load_gate_liveness_result",
                model_id,
                None,
                process_status=(
                    process_status.value
                    if hasattr(process_status, "value")
                    else str(process_status)
                ),
                alive=alive,
            )
            return alive
        except (ProcessError, Exception) as e:
            await _emit_embedding_debug(
                "load_gate_liveness_exception",
                model_id,
                None,
                error_type=type(e).__name__,
                error=str(e),
            )
            return False

    async def get_status(self) -> dict[str, Any]:
        workers = await self.get_workers_status()
        return {
            "active_models": list(workers.keys()),
            "workers_status": workers,
            "auto_load_enabled": self.auto_load_on_request,
        }

    def get_worker_metrics(self, model_id: str) -> dict | None:
        """
        Get metrics for a specific worker.

        Args:
            model_id: Canonical model ID (no `:N` instance suffix)

        Returns:
            Dict with worker metrics or None if worker not found
        """
        sup = self._process_state.get_supervisor(model_id)
        if not sup:
            return None

        # Get worker status
        try:
            status = sup.get_worker_status()
            if hasattr(status, "value"):
                status_value = status.value
            elif status:
                status_value = str(status)
            else:
                status_value = "unknown"
        except Exception:
            status_value = "unknown"

        return {
            "model_id": model_id,
            "status": status_value,
        }

    def get_all_worker_metrics(self) -> dict[str, dict]:
        """
        Get metrics for all workers.

        Returns:
            Dict mapping model_id to metrics dict
        """
        metrics = {}
        for model_id in self._process_state.supervisors.keys():
            worker_metrics = self.get_worker_metrics(str(model_id))
            if worker_metrics:
                metrics[model_id] = worker_metrics
        return metrics

    def get_loaded_workers(self) -> list[str]:
        """
        Get list of all loaded model IDs.

        Returns:
            List of canonical model IDs (no `:N` instance suffix)
        """
        return [str(model_id) for model_id in self._process_state.supervisors.keys()]

    def get_running_worker_processes(self) -> dict[str, int]:
        """Return active worker processes as model_id -> pid mapping."""
        return self._process_state.get_running_worker_processes()

    async def cleanup_orphaned_process(self, model_id: str) -> bool:
        """
        Clean up an orphaned process (manual intervention).

        Used by cleanup API endpoint when automatic cleanup has failed.

        Returns True if cleanup successful.
        """
        process_info = self.get_all_process_info().get(model_id, {})
        pid = process_info.get("pid") if isinstance(process_info, dict) else None

        if pid:
            try:
                import psutil

                if psutil.pid_exists(pid):
                    await self._lifecycle_manager.kill_pid_tree(pid, model_id)
            except Exception as e:
                logger.error(f"Force kill failed for {model_id}: {e}")

        # Clean up all state regardless
        _get_resource_tracker().unregister_model(model_id)

        await self._cleanup_socket_file(model_id)
        self._process_state.remove_supervisor(model_id)
        self._process_state.remove_socket_path(model_id)

        logger.info(f"✅ Cleaned up orphaned process: {model_id}")
        return True

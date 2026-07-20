"""Process health, status, and resource query mixin."""

from typing import Any


class ProcessStatusMixin:
    """Worker process info, health checks, and resource usage queries."""

    def get_all_process_info(self) -> dict[str, Any]:
        def build(model_name, sup):
            try:
                status, info = sup.get_worker_status(), sup.get_worker_info()
                s_val = (
                    status.value
                    if hasattr(status, "value")
                    else str(status)
                    if status
                    else "unknown"
                )
                return {
                    "model_id": str(model_name),
                    "status": s_val,
                    "pid": info.pid if info else None,
                    "socket_path": self._process_state.get_socket_path(model_name),
                }
            except Exception as exc:
                return {
                    "model_id": str(model_name),
                    "status": "error",
                    "error": str(exc),
                }

        # Return dict keyed by normalized model IDs
        # (supervisors dict already uses normalized string keys)
        return {
            model_id: build(model_id, sup)
            for model_id, sup in self._process_state.supervisors.items()
        }

    async def get_workers_status(self) -> dict[str, Any]:
        return self.get_all_process_info()

    async def is_process_alive(self, model_id: str) -> bool:
        return await self._lifecycle_manager.is_process_alive(model_id)

    async def check_engine_health(self, model_id: str) -> bool:
        """Check if the model's inference engine is alive (not just the worker process).

        Sends a ``health`` RPC to the worker which checks
        ``engine.is_loaded()`` — returns True only when the underlying
        llama-server (or other engine subprocess) is running.
        """
        supervisor = self._process_state.get_supervisor(model_id)
        if not supervisor:
            return False
        try:
            result = await supervisor.execute_command(
                {"command_type": "health"}, timeout=5.0
            )
            return bool(result and result.get("model_loaded"))
        except Exception:
            return False

    async def get_process_status(self, model_id: str):
        sup = self._process_state.get_supervisor(model_id)
        if not sup:
            return None
        try:
            status = sup.get_worker_status()
            if status and hasattr(status, "value") and status.value == "DEAD":
                self._process_state.remove_supervisor(model_id)
                self._process_state.remove_socket_path(model_id)
                await self._cleanup_socket_file(model_id)
                return None
            return status
        except Exception:
            self._process_state.remove_supervisor(model_id)
            self._process_state.remove_socket_path(model_id)
            await self._cleanup_socket_file(model_id)
            return None

    def get_worker_info(self, model_id: str):
        sup = self._process_state.get_supervisor(model_id)
        return sup.get_worker_info() if sup else None

    def get_engine_pid(self, model_id: str) -> int | None:
        """Get engine subprocess PID for ghost detection."""
        return self._process_state.get_engine_pid(model_id)

    def get_socket_path(self, model_id: str) -> str:
        return self._communication_manager.get_socket_path(model_id)

    async def get_resource_usage(self, model_id: str):
        return await self._resource_monitor.get_resource_usage(model_id)

    def get_peak_usage(self, model_id: str):
        return self._resource_monitor.get_peak_usage(model_id)

    def reset_peak_usage(self, model_id: str):
        self._resource_monitor.reset_peak_usage(model_id)

    async def get_model_info(self, model_id: str) -> dict[str, Any]:
        sup = self._process_state.get_supervisor(model_id)
        if not sup:
            return {"error": f"No supervisor for {model_id}"}
        try:
            timeout = float(
                getattr(self.gateway_config.process_isolation, "model_info_timeout", 30)
            )
            payload = await sup.execute_command(
                {"command_type": "get_model_info"}, timeout=timeout
            )
            return (
                payload.get("model_info", {})
                if "error" not in payload
                else {"error": payload["error"]}
            )
        except Exception as exc:
            return {"error": str(exc)}

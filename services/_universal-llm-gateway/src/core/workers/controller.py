"""Worker controller delegating model loading and inference."""

import os
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

from process_ipc import ProcessHealthConfig, ProcessStatus
from process_ipc.core.exceptions import ProcessError
from universal_event_bus.events.debug import emit_debug_event
from universal_logging import get_logger

from src.core.model_registry import ModelRegistry

from .chat_completion import NonStreamingChatCompletion, StreamingChatCompletion
from .entrypoint import WorkerEntrypoint
from .inference import (
    InferenceCancellationManager,
    RegularInferenceManager,
    StreamingInferenceManager,
)
from .model_operations import ModelLoader, ModelUnloader, UnloadResult
from .monitoring import ProcessMonitor
from .process import ProcessCommunicationManager, ProcessLifecycleManager, ProcessState
from .utils import get_python_executable


def _get_resource_tracker():
    from src.core.resources import resource_tracker

    return resource_tracker


logger = get_logger(__name__)
structured_logger = get_logger("universal_llm_gateway.controller")


async def _emit_embedding_debug(
    step: str,
    model_id: str,
    correlation_id: str | None,
    **extra: Any,
) -> None:
    """Emit a temporary debug event for embedding request tracing."""
    payload: dict[str, Any] = {
        "step": step,
        "component": "controller",
        "model_id": model_id,
    }
    if correlation_id:
        payload["correlation_id"] = correlation_id
    payload.update(extra)
    await emit_debug_event("debug.embedding.gateway", payload, source="gateway")


class WorkerController:
    """
    Worker controller delegating to loader/unloader and chat handlers.

    Uses canonical model IDs only (no `:N` instance suffix).
    One worker per model_id.
    """

    def __init__(
        self, model_registry: ModelRegistry, gateway_config: Any, event_bus=None
    ):
        self.gateway_config, self.model_registry, self.event_bus = (
            gateway_config,
            model_registry,
            event_bus,
        )
        self.auto_load_on_request = gateway_config.models.auto_load_on_request
        self._init_resource_monitoring(event_bus)
        self._process_state = ProcessState()
        self._init_paths(gateway_config)
        self._init_managers(gateway_config, event_bus)
        self._model_loader, self._model_unloader = (
            ModelLoader(self),
            ModelUnloader(self),
        )
        self._chat_non_streaming, self._chat_streaming = (
            NonStreamingChatCompletion(self),
            StreamingChatCompletion(self),
        )
        # Idle callbacks for graceful shutdown (event-driven)
        self._idle_callbacks: list[Callable[[], Awaitable[None]]] = []

        logger.info(
            f"🔧 WorkerController initialized (auto_load: {self.auto_load_on_request})"
        )

    def _init_resource_monitoring(self, event_bus) -> None:
        """Set up resource monitoring config if event_bus is provided."""
        self.resource_monitor_enabled, self.resource_config = False, None
        if event_bus:
            try:
                from process_ipc import ResourceMonitoringConfig

                self.resource_config = ResourceMonitoringConfig(
                    enable_resource_monitoring=True,
                    enable_gpu_monitoring=True,
                    monitoring_interval=1.0,
                    history_size=1000,
                )
                self.resource_monitor_enabled = True
            except Exception as e:
                logger.warning(
                    f"⚠️ Resource monitoring initialization failed: {e}. "
                    f"Continuing without resource monitoring."
                )

    def _init_paths(self, cfg) -> None:
        """Set worker log dir, IPC socket dir, entrypoint, and timeouts from config."""
        iso = cfg.process_isolation
        # Respect WORKER_LOG_DIR from environment
        default_dir = os.getenv("WORKER_LOG_DIR", "/tmp/llm_gateway/worker-logs")
        self.worker_logs_dir = Path(getattr(iso, "worker_logs_dir", default_dir))
        self.ipc_socket_dir = Path("/tmp/universal-protocol")
        self.worker_logs_dir.mkdir(parents=True, exist_ok=True)
        self.ipc_socket_dir.mkdir(parents=True, exist_ok=True)
        self.python_executable = get_python_executable(cfg)

        # Worker entrypoint: module invocation from service root
        # cwd must be the directory containing src/ for `python -m src.core...` to work
        service_root = Path(__file__).parent.parent.parent.parent
        self.worker_entrypoint = WorkerEntrypoint.as_module(
            module_name="src.core.workers.worker",
            cwd=service_root,
        )

        self.startup_timeout, self.shutdown_timeout = (
            float(getattr(iso, "startup_timeout", 300)),
            float(getattr(iso, "shutdown_timeout", 30)),
        )

    def _init_managers(self, cfg, event_bus) -> None:
        """Create lifecycle, communication, and inference managers."""
        hc = self._create_health_config(cfg, event_bus)
        self._lifecycle_manager = ProcessLifecycleManager(
            state=self._process_state,
            worker_logs_dir=self.worker_logs_dir,
            ipc_socket_dir=self.ipc_socket_dir,
            gateway_config=cfg,
            python_executable=self.python_executable,
            worker_entrypoint=self.worker_entrypoint,
            health_config=hc,
            resource_config=self.resource_config,
            startup_timeout=self.startup_timeout,
            shutdown_timeout=self.shutdown_timeout,
        )
        self._communication_manager = ProcessCommunicationManager(
            state=self._process_state,
            ipc_socket_dir=self.ipc_socket_dir,
            gateway_config=cfg,
            model_registry=self.model_registry,
        )
        self._resource_monitor = ProcessMonitor(process_state=self._process_state)
        self._regular_inference = RegularInferenceManager(
            process_state=self._process_state, gateway_config=cfg
        )
        self._streaming_inference = StreamingInferenceManager(
            process_state=self._process_state, gateway_config=cfg, event_bus=event_bus
        )
        self._inference_cancellation = InferenceCancellationManager(
            process_state=self._process_state
        )

    def _create_health_config(self, cfg, event_bus) -> ProcessHealthConfig:
        """Build ProcessHealthConfig from gateway config and event bus."""
        hm = getattr(cfg.process_isolation, "health_monitoring", {})

        async def cb(pid, code, msg):
            await self._lifecycle_manager.handle_process_crash_callback(pid, code, msg)

        return ProcessHealthConfig(
            auto_recovery=hm.get("auto_recovery", False),
            health_check_interval=hm.get("check_interval", 15.0),
            health_check_timeout=3.0,
            max_recovery_attempts=3,
            recovery_backoff=5.0,
            recovery_timeout=60.0,
            verify_process_status=True,
            log_recovery_attempts=True,
            log_health_checks=False,
            background_monitoring=True,
            start_monitoring_on_state=hm.get("start_monitoring_on_state", "READY"),
            capture_error_output=True,
            max_error_output_size=1048576,
            preserve_error_output=True,
            detect_crashes=True,
            event_bus=event_bus,
            capture_stderr_on_crash=True,
            crash_exit_codes=None,
            expected_exit_codes=[0],
            on_process_crash=cb,
            crash_callback_timeout=10.0,
        )

    async def _cleanup_socket_file(self, model_id: str) -> None:
        from .utils import cleanup_socket_file

        sp = self._process_state.get_socket_path(model_id)
        if sp:
            cleanup_socket_file(sp)

    def _create_transport_config(self, socket_path: str):
        return self._communication_manager.create_transport_config(socket_path)

    async def start(self):
        logger.info("🚀 Starting WorkerController...")
        # Subscribe to INFERENCE_COMPLETED to check for idle state (graceful shutdown)
        if self.event_bus:
            try:
                from src.core.events.types import INFERENCE_COMPLETED

                self.event_bus.subscribe_async(
                    INFERENCE_COMPLETED, self._on_inference_completed
                )
            except Exception as e:
                logger.warning(f"Could not subscribe to INFERENCE_COMPLETED: {e}")

    async def _on_inference_completed(self, event) -> None:
        """Handle INFERENCE_COMPLETED event to check for idle state."""
        await self._notify_idle_if_needed()

    async def stop(self):
        logger.info("🛑 Stopping WorkerController...")
        await self.shutdown()

    async def shutdown(self):
        logger.info("🛑 Shutting down WorkerController...")
        if await self._lifecycle_manager.shutdown():
            for mid in list(_get_resource_tracker().get_all_models_info().keys()):
                _get_resource_tracker().unregister_model(mid)
            return True
        return False

    def is_idle(self) -> bool:
        """Check if controller has no in-flight work. Used for graceful shutdown."""
        try:
            return len(_get_resource_tracker().get_busy_models()) == 0
        except Exception:
            return False

    def register_idle_callback(self, callback: Callable[[], Awaitable[None]]) -> None:
        """Register callback to be called when controller becomes idle.

        Used for graceful shutdown to notify when all work is complete.
        """
        self._idle_callbacks.append(callback)

    async def _notify_idle_if_needed(self) -> None:
        """Notify idle callbacks if controller is now idle."""
        if not self._idle_callbacks:
            return
        if not self.is_idle():
            return
        for callback in self._idle_callbacks:
            try:
                await callback()
            except Exception as e:
                logger.error(f"Idle callback error: {e}")

    async def load_model(self, model_id: str) -> bool:
        return await self._model_loader.load_model(model_id)

    async def ensure_model_loaded(
        self, model_id: str, correlation_id: str | None = None
    ) -> bool:
        return await self._model_loader.ensure_model_loaded(model_id, correlation_id)

    async def unload_model(self, model_id: str, force: bool = False) -> UnloadResult:
        """Unload a model. Returns UnloadResult with success/skip status.

        Args:
            model_id: Model to unload
            force: If True, kill process immediately bypassing busy check
        """
        return await self._model_unloader.unload_model(model_id, force=force)

    async def unload_current_model(self) -> UnloadResult:
        """Unload current model. Returns UnloadResult with success/skip status."""
        return await self._model_unloader.unload_current_model()

    async def inference(
        self,
        model_id: str,
        messages: list,
        parameters: dict,
        correlation_id: str | None = None,
    ):
        return await self._chat_non_streaming.inference(
            model_id, messages, parameters, correlation_id
        )

    async def generate_chat_completion(
        self, model_id: str, messages: list, correlation_id: str | None = None, **kwargs
    ):
        return await self._chat_non_streaming.generate_chat_completion(
            model_id, messages, correlation_id, **kwargs
        )

    async def count_tokens(
        self,
        model_id: str,
        message_or_prompt: list | str,
        use_cpu: bool,
        context_length: int | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict:
        return await self._chat_non_streaming.count_tokens(
            model_id, message_or_prompt, use_cpu, context_length, tools=tools
        )

    async def inference_stream(
        self,
        model_id: str,
        messages: list,
        parameters: dict,
        correlation_id: str | None = None,
    ) -> AsyncIterator[dict]:
        async for chunk in self._chat_streaming.inference_stream(
            model_id, messages, parameters, correlation_id
        ):
            yield chunk

    async def generate_chat_completion_stream(
        self, model_id: str, messages: list, correlation_id: str | None = None, **kwargs
    ) -> AsyncIterator[dict]:
        async for chunk in self._chat_streaming.generate_chat_completion_stream(
            model_id, messages, correlation_id, **kwargs
        ):
            yield chunk

    async def cancel_streaming_inference(
        self, model_id: str, stream_id: str, reason: str = "explicit_cancellation"
    ) -> bool:
        return await self._inference_cancellation.cancel_streaming_inference(
            model_id, stream_id, reason
        )

    async def cancel_current_stream(self, model_id: str) -> bool:
        return await self._inference_cancellation.cancel_current_stream(model_id)

    async def cancel_work(
        self,
        model_id: str,
        stream_id: str | None = None,
        reason: str = "explicit_cancellation",
    ) -> bool:
        """
        Cancel active work on a model.

        Event-driven state update via STREAM_CANCELLED when RPC succeeds.
        On RPC failure, sets ERROR (worker may still be busy).

        Invariant: ∀ successful cancellation, idle update via event consumer
                   (∃! writer).
        """
        success = await self._cancel_work_rpc(model_id, stream_id, reason)

        if success:
            from .cancellation import emit_stream_cancelled_or_force_idle

            await emit_stream_cancelled_or_force_idle(
                model_id, stream_id, reason, event_bus=self.event_bus
            )
        else:
            await self._handle_rpc_cancel_failure(model_id, reason)

        return success

    async def _cancel_work_rpc(
        self, model_id: str, stream_id: str | None, reason: str
    ) -> bool:
        """Call RPC cancellation (no tracker updates)."""
        return await self._inference_cancellation.cancel_work(
            model_id, stream_id, reason
        )

    async def _handle_rpc_cancel_failure(self, model_id: str, reason: str) -> None:
        """
        Handle RPC cancellation failure: mark model as ERROR.

        Worker may still be busy; forcing idle would allow concurrent requests.
        """
        logger.error(f"❌ RPC cancellation failed for {model_id}, marking as ERROR")
        _get_resource_tracker().set_model_error(
            model_id, f"Cancellation RPC failed: {reason}"
        )

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

    async def _call_rpc(
        self, model_id: str, method: str, params: dict[str, Any], timeout: float = 300.0
    ) -> dict[str, Any]:
        """
        Internal: Call an RPC method on the worker for the specified model.

        Args:
            model_id: Model ID (must be loaded)
            method: RPC method name to call
            params: Parameters to pass to the RPC method
            timeout: Request timeout in seconds

        Returns:
            RPC response data

        Raises:
            RuntimeError: If model not loaded or RPC fails
        """
        sup = self._process_state.get_supervisor(model_id)
        if not sup:
            raise RuntimeError(f"No supervisor found for model {model_id}")

        if not sup._http_client:
            raise RuntimeError(f"HTTP client not initialized for model {model_id}")

        return await sup._inference_rpc_call(method, params, timeout=timeout)

    async def call_rpc(
        self, model_id: str, method: str, params: dict[str, Any], timeout: float = 300.0
    ) -> Any:
        """
        Public: Call an RPC method on the worker for the specified model.

        Args:
            model_id: Model ID (must be loaded)
            method: RPC method name to call
            params: Parameters to pass to the RPC method
            timeout: Request timeout in seconds

        Returns:
            RPC response data

        Raises:
            RuntimeError: If model not loaded or RPC fails
        """
        return await self._call_rpc(model_id, method, params, timeout)

    async def transcribe_file(
        self,
        model_id: str,
        audio_file_path: str,
        language: str | None = None,
        prompt: str | None = None,
        temperature: float = 0.0,
        word_timestamps: bool = True,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """
        Transcribe an audio file using a Whisper model.

        Args:
            model_id: Whisper model ID (must be loaded)
            audio_file_path: Path to audio file on disk
            language: Optional language code (e.g., "en", "fa")
            prompt: Optional text to guide transcription style
            temperature: Sampling temperature (0.0 to 1.0)
            word_timestamps: Whether to include word-level timestamps
            timeout: Optional processing timeout in seconds

        Returns:
            Transcription result with text, language, duration, segments

        Raises:
            RuntimeError: If model not loaded or transcription fails
            TimeoutError: If timeout exceeded
        """
        params = {
            "audio_file_path": audio_file_path,
            "language": language,
            "prompt": prompt,
            "temperature": temperature,
            "word_timestamps": word_timestamps,
        }

        rpc_timeout = timeout if timeout is not None else 300.0
        return await self._call_rpc(model_id, "transcribe_file", params, rpc_timeout)

    async def generate_image(
        self,
        model_id: str,
        prompt: str,
        width: int = 1024,
        height: int = 1024,
        num_inference_steps: int = 20,  # FLUX.2 default
        guidance_scale: float = 4.0,  # FLUX.2 default
        seed: int | None = None,
        response_format: str = "b64_json",
        negative_prompt: str | None = None,
        caption_upsample_temperature: float | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """
        Generate an image using a Flux.2 model.

        Args:
            model_id: Flux.2 model ID (must be loaded)
            prompt: Text prompt for image generation
            width: Image width in pixels
            height: Image height in pixels
            num_inference_steps: Number of denoising steps
            guidance_scale: Guidance strength
            seed: Optional random seed for reproducibility
            response_format: Response format ("url" or "b64_json")
            negative_prompt: Optional negative prompt (what to avoid)
            caption_upsample_temperature: Optional caption upsampling temp
                (0.15 recommended for FLUX.2)
            timeout: Optional processing timeout in seconds

        Returns:
            Image generation result with created timestamp and data list

        Raises:
            RuntimeError: If model not loaded or generation fails
            TimeoutError: If timeout exceeded
        """
        params = {
            "prompt": prompt,
            "width": width,
            "height": height,
            "num_inference_steps": num_inference_steps,
            "guidance_scale": guidance_scale,
            "seed": seed,
            "response_format": response_format,
            "negative_prompt": negative_prompt,
            "caption_upsample_temperature": caption_upsample_temperature,
        }

        rpc_timeout = timeout if timeout is not None else 300.0

        # Wrap with inference tracking (emits INFERENCE_STARTED/COMPLETED events)
        resource_tracker = _get_resource_tracker()
        async with resource_tracker.track_inference(model_id):
            return await self._call_rpc(model_id, "generate_image", params, rpc_timeout)

    async def generate_embeddings(
        self,
        model_id: str,
        input_texts: list[str],
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Generate embeddings via worker RPC.

        Args:
            model_id: Model identifier
            input_texts: Texts to embed
            correlation_id: Request correlation ID

        Returns:
            OpenAI-compatible embedding response

        Raises:
            RuntimeError: If model not loaded, RPC fails, or timeout triggers quarantine
        """
        params = {
            "input": input_texts,
            "model": model_id,
            "correlation_id": correlation_id,
        }

        resource_tracker = _get_resource_tracker()
        try:
            async with resource_tracker.track_inference(model_id):
                result = await self._call_rpc(model_id, "generate_embeddings", params)
        except TimeoutError as exc:
            # track_inference already set model to IDLE — override to ERROR.
            # The worker thread is likely still stuck on the llama-server call;
            # force-recycle kills the worker (and its llama-server child) to
            # release GPU memory and prevent capacity from being overstated.
            resource_tracker.set_model_error(
                model_id, f"Embedding timeout — worker recycled: {exc}"
            )
            await self._recycle_after_embedding_timeout(model_id, correlation_id)
            raise RuntimeError(
                f"Embedding timed out — worker process terminated for {model_id}"
            ) from exc
        return result

    async def _recycle_after_embedding_timeout(
        self, model_id: str, correlation_id: str | None = None
    ) -> None:
        """Force-recycle worker after embedding timeout to release GPU resources.

        Kills the worker process tree (including llama-server child), removes
        supervisor tracking, and emits a quarantine event. The model remains in
        ERROR state; auto-load-on-request will reload it when the next request
        arrives.
        """
        logger.error(f"⏰ Embedding timeout for {model_id} — killing worker process")
        await _emit_embedding_debug(
            "timeout_quarantine",
            model_id,
            correlation_id,
            action="force_recycle",
        )
        sup = self._process_state.get_supervisor(model_id)
        if sup:
            try:
                await sup.stop(force=True, timeout=5)
                logger.info(f"✅ Killed timed-out embedding worker for {model_id}")
            except Exception as e:
                logger.error(
                    f"Failed to kill timed-out embedding worker for {model_id}: {e}"
                )
        self._process_state.remove_supervisor(model_id)
        self._process_state.remove_socket_path(model_id)
        await self._cleanup_socket_file(model_id)

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

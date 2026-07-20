"""Worker controller delegating model loading and inference."""

import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from process_ipc import ProcessHealthConfig

from src.core.model_registry import ModelRegistry

from ..chat_completion import NonStreamingChatCompletion, StreamingChatCompletion
from ..entrypoint import WorkerEntrypoint
from ..inference import (
    InferenceCancellationManager,
    RegularInferenceManager,
    StreamingInferenceManager,
)
from ..model_operations import ModelLoader, ModelUnloader
from ..monitoring import ProcessMonitor
from ..process import ProcessCommunicationManager, ProcessLifecycleManager, ProcessState
from ..utils import get_python_executable
from ._runtime import logger
from .cancel_rpc import CancelRpcMixin
from .inference_facade import InferenceFacadeMixin
from .lifecycle import LifecycleMixin
from .metrics import MetricsMixin
from .model_facade import ModelFacadeMixin
from .multimodal import MultimodalMixin
from .process_status import ProcessStatusMixin


class WorkerController(
    LifecycleMixin,
    ModelFacadeMixin,
    InferenceFacadeMixin,
    CancelRpcMixin,
    MultimodalMixin,
    ProcessStatusMixin,
    MetricsMixin,
):
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
        service_root = Path(__file__).parent.parent.parent.parent.parent
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
        from ..utils import cleanup_socket_file

        sp = self._process_state.get_socket_path(model_id)
        if sp:
            cleanup_socket_file(sp)

    def _create_transport_config(self, socket_path: str):
        return self._communication_manager.create_transport_config(socket_path)

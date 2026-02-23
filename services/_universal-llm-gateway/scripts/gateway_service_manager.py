#!/usr/bin/env python3
"""
Universal LLM Gateway Service Manager

"""

import atexit
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import psutil

# Repo root — derived from this file's position in the tree.
# {repo}/services/_universal-llm-gateway/scripts/gateway_service_manager.py
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent.parent)

# Early path setup for universal_logging import
_libs_path = Path(_PROJECT_ROOT) / "libs"
if _libs_path.exists() and str(_libs_path) not in sys.path:
    sys.path.insert(0, str(_libs_path))

from universal_logging import get_logger  # noqa: E402

# Note: Environment loading is handled by wrapper script (start-gateway.sh)
# which sources .env.local from project root before starting this Python service manager.
# All config is read from os.environ.


# Thread configuration validation (canonical version in core.config_loader)
def _validate_thread_count(value: str | None, param_name: str) -> int | None:
    """
    Validate thread count configuration with bounds checking.

    Canonical implementation: src/core/config_loader.validate_thread_count()
    This is a service manager copy for bootstrap purposes.

    Args:
        value: Thread count from environment variable
        param_name: Parameter name for error messages

    Returns:
        Validated thread count or None if not set

    Raises:
        ValueError: If thread count is invalid
    """
    if value is None:
        return None

    try:
        threads = int(value)
    except ValueError:
        raise ValueError(f"Invalid {param_name}: '{value}' is not a valid integer")

    # Bounds checking: 1 <= threads <= 256
    if threads < 1:
        raise ValueError(f"Invalid {param_name}: {threads} is below minimum (1)")
    if threads > 256:
        raise ValueError(f"Invalid {param_name}: {threads} exceeds maximum (256)")

    return threads


@dataclass
class GatewayConfig:
    """
    Gateway configuration with validation and defaults.

    Configuration is loaded from environment variables only.
    The wrapper script (start-gateway.sh) sources .env.local from the project root
    before starting this service manager.
    """

    # Core service configuration
    host: str = "0.0.0.0"
    port: int = 9998
    unix_socket: str | None = None
    log_level: str = "info"
    gateway_api_key: str = ""

    # Path configuration
    gateway_venv: str = field(
        default_factory=lambda: os.path.expanduser("~/.venvs/universal")
    )
    workdir: str = ""
    project_root: str = ""

    # Resource configuration
    metrics_message_retention: int = 10000
    metrics_error_retention: int = 1000
    metrics_queue_size: int = 1000
    metrics_queue_timeout: float = 0.1

    # CUDA configuration
    cuda_visible_devices: str = "0"
    cuda_home: str = "/usr/local/cuda"
    torch_cuda_arch_list: str = "12.0"

    # Cache and storage
    model_cache_dir: str = "~/.models"

    # Process configuration (respect WORKER_LOG_DIR from environment)
    worker_log_dir: str = field(
        default_factory=lambda: os.getenv(
            "WORKER_LOG_DIR", "/tmp/llm_gateway/worker-logs"
        )
    )
    process_startup_timeout: int = 300

    # Feature flags
    debug_mode: bool = False
    enable_profiling: bool = False
    enable_management_api: bool = True
    disable_health_checking: bool = True
    disable_uvicorn_access_log: bool = True
    # Check availability by default (filters /v1/models to available paths only)
    enable_model_availability_check: bool = True
    fast_shutdown: bool = False  # Fast shutdown for development (immediate SIGKILL)

    # Threading configuration (Optional - auto-detect if not set)
    # Platform-specific CPU optimization - only set if explicitly provided
    omp_num_threads: int | None = None  # OpenMP threads (auto-detect optimal value)
    mkl_num_threads: int | None = None  # Intel MKL threads (Intel CPUs only)
    tokenizers_parallelism: str | None = None  # HuggingFace tokenizers parallelism

    @classmethod
    def from_environment(cls, environment: str = "default") -> "GatewayConfig":
        """
        Load configuration from environment variables.

        Environment variables are loaded by wrapper script (start-gateway.sh)
        which sources .env.local from the project root.

        Args:
            environment: Environment name (default, debug, release)

        Returns:
            Configured GatewayConfig instance
        """

        # Create config from environment variables
        config = cls(
            host=os.getenv("GATEWAY_HOST", "0.0.0.0"),
            port=int(os.getenv("GATEWAY_PORT", "9998")),
            unix_socket=os.getenv("GATEWAY_UNIX_SOCKET"),
            log_level=os.getenv("LOG_LEVEL", "info").lower(),
            gateway_api_key=os.getenv("GATEWAY_API_KEY", ""),
            gateway_venv=os.getenv(
                "GATEWAY_VENV", os.path.expanduser("~/.venvs/universal")
            ),
            metrics_message_retention=int(
                os.getenv("METRICS_MESSAGE_RETENTION", "10000")
            ),
            metrics_error_retention=int(os.getenv("METRICS_ERROR_RETENTION", "1000")),
            metrics_queue_size=int(os.getenv("METRICS_QUEUE_SIZE", "1000")),
            metrics_queue_timeout=float(os.getenv("METRICS_QUEUE_TIMEOUT", "0.1")),
            cuda_visible_devices=os.getenv("CUDA_VISIBLE_DEVICES", "0"),
            cuda_home=os.getenv("CUDA_HOME", "/usr/local/cuda"),
            torch_cuda_arch_list=os.getenv("TORCH_CUDA_ARCH_LIST", "12.0"),
            model_cache_dir=os.getenv(
                "MODEL_CACHE_DIR", os.path.expanduser("~/.models")
            ),
            worker_log_dir=os.getenv("WORKER_LOG_DIR", "/tmp/llm_gateway/worker-logs"),
            process_startup_timeout=int(os.getenv("PROCESS_STARTUP_TIMEOUT", "300")),
            debug_mode=os.getenv("DEBUG_MODE", "false").lower() == "true",
            enable_profiling=os.getenv("ENABLE_PROFILING", "false").lower() == "true",
            enable_management_api=os.getenv("ENABLE_MANAGEMENT_API", "true").lower()
            == "true",
            disable_health_checking=os.getenv("DISABLE_HEALTH_CHECKING", "true").lower()
            == "true",
            disable_uvicorn_access_log=os.getenv(
                "DISABLE_UVICORN_ACCESS_LOG", "true"
            ).lower()
            == "true",
            enable_model_availability_check=os.getenv(
                "ENABLE_MODEL_AVAILABILITY_CHECK", "true"
            ).lower()
            == "true",
            fast_shutdown=os.getenv("FAST_SHUTDOWN", "false").lower() == "true",
            # CPU optimization (platform-specific - validated if provided)
            omp_num_threads=_validate_thread_count(
                os.getenv("OMP_NUM_THREADS"), "OMP_NUM_THREADS"
            ),
            mkl_num_threads=_validate_thread_count(
                os.getenv("MKL_NUM_THREADS"), "MKL_NUM_THREADS"
            ),
            tokenizers_parallelism=(
                tok_val if (tok_val := os.getenv("TOKENIZERS_PARALLELISM")) else None
            ),
            project_root=_PROJECT_ROOT,
        )

        config.workdir = os.getenv("GATEWAY_WORKDIR") or str(
            Path(config.project_root) / "services" / "_universal-llm-gateway"
        )

        return config

    def validate(self) -> list[str]:
        """
        Validate configuration and return list of validation errors.

        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []

        # Validate port range
        if not (1024 <= self.port <= 65535):
            errors.append(f"Port {self.port} must be between 1024 and 65535")

        # Validate log level
        valid_levels = ["debug", "info", "warning", "error", "critical"]
        if self.log_level not in valid_levels:
            errors.append(
                f"Log level '{self.log_level}' must be one of: {valid_levels}"
            )

        # Validate virtual environment
        python_exec = Path(self.gateway_venv) / "bin" / "python"
        if not python_exec.exists():
            errors.append(f"Python executable not found: {python_exec}")

        # Validate working directory
        workdir_path = Path(self.workdir)
        if not workdir_path.exists():
            errors.append(f"Working directory not found: {self.workdir}")

        # Validate uvicorn main module
        main_module = workdir_path / "src" / "main.py"
        if not main_module.exists():
            errors.append(f"Main module not found: {main_module}")

        return errors

    @property
    def python_executable(self) -> str:
        """Get path to Python executable in virtual environment"""
        return str(Path(self.gateway_venv) / "bin" / "python")

    @property
    def pid_file(self) -> str:
        """Get port-specific PID file path"""
        if self.unix_socket:
            # Use socket path hash for unique PID file
            import hashlib

            socket_hash = hashlib.md5(self.unix_socket.encode()).hexdigest()[:8]
            return f"/tmp/universal-llm-gateway-unix-{socket_hash}.pid"
        return f"/tmp/universal-llm-gateway-{self.port}.pid"

    # Logging configuration is now handled automatically by the application


class ProcessManager:
    """
    Robust process management using psutil.

    Handles process discovery, termination, and cleanup much more reliably
    than the complex bash string manipulation in the original script.
    """

    def __init__(self, logger: logging.Logger):
        self.logger = logger

    def find_gateway_processes(
        self, port: int | None = None, unix_socket: str | None = None
    ) -> list[psutil.Process]:
        """
        Find all gateway-related processes.

        Args:
            port: Gateway port to search for (TCP mode)
            unix_socket: Unix socket path to search for (Unix socket mode)

        Returns:
            List of Process objects for gateway-related processes
        """
        processes = []
        current_pid = os.getpid()  # Exclude our own PID

        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                # Skip our own process to avoid self-termination
                if proc.pid == current_pid:
                    continue

                cmdline = " ".join(proc.info["cmdline"] or [])

                # Check for uvicorn processes
                if "uvicorn" in cmdline and "src.main:app" in cmdline:
                    # Check for TCP mode
                    if port and f"--port {port}" in cmdline:
                        processes.append(proc)
                        self.logger.debug(
                            f"Found gateway uvicorn process (TCP): PID {proc.pid}"
                        )
                    # Check for Unix socket mode
                    elif unix_socket and f"--uds {unix_socket}" in cmdline:
                        processes.append(proc)
                        self.logger.debug(
                            f"Found gateway uvicorn process (Unix socket): "
                            f"PID {proc.pid}"
                        )

                # Check for worker processes (excluding service manager scripts)
                elif (
                    any(
                        pattern in cmdline
                        for pattern in [
                            "worker.py",
                            "supervisor.py",
                            "inference_djinn",
                            "process_ipc",
                        ]
                    )
                    and "universal-llm-gateway" in cmdline
                    and "gateway_service_manager.py" not in cmdline
                ):
                    processes.append(proc)
                    self.logger.debug(f"Found gateway worker process: PID {proc.pid}")

            except (psutil.NoSuchProcess, psutil.AccessDenied):
                # Process disappeared or access denied
                continue

        return processes

    def find_processes_on_port(self, port: int) -> list[psutil.Process]:
        """
        Find processes listening on specific port.

        Args:
            port: Port number to check

        Returns:
            List of processes using the port
        """
        processes = []

        for proc in psutil.process_iter(["pid", "name"]):
            try:
                connections = proc.connections(kind="inet")
                for conn in connections:
                    if conn.laddr.port == port:
                        processes.append(proc)
                        self.logger.debug(
                            f"Found process on port {port}: PID {proc.pid}"
                        )

            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        return processes

    def get_process_tree(self, process: psutil.Process) -> list[psutil.Process]:
        """
        Get all child processes recursively.

        Args:
            process: Parent process

        Returns:
            List of all descendant processes
        """
        children = []

        try:
            for child in process.children(recursive=True):
                children.append(child)
                self.logger.debug(f"Found child process: PID {child.pid}")
        except psutil.NoSuchProcess:
            pass

        return children

    def terminate_process_tree(
        self, process: psutil.Process, timeout: int = 3, sigint_first: bool = True
    ) -> bool:
        """
        Terminate a process and all its children.

        Args:
            process: Root process to terminate
            timeout: Total seconds for termination (default 3s)
            sigint_first: Send SIGINT before SIGTERM (graceful for uvicorn)

        Returns:
            True if all processes terminated successfully
        """
        if not process.is_running():
            return True

        # Get all children first
        children = self.get_process_tree(process)
        all_processes = children + [process]

        self.logger.info(f"Terminating process tree: {len(all_processes)} processes")

        # Phase 1: SIGINT to root process (if requested)
        if sigint_first:
            try:
                self.logger.debug(f"Sending SIGINT to PID {process.pid}")
                process.send_signal(signal.SIGINT)
                # Short wait for SIGINT handling (1s of 3s budget)
                gone, alive = psutil.wait_procs([process], timeout=1)
                if not alive:
                    self.logger.info("Process terminated on SIGINT")
                    return True
                all_processes = [p for p in all_processes if p.is_running()]
            except psutil.NoSuchProcess:
                return True

        # Phase 2: SIGTERM to all remaining processes
        for proc in all_processes:
            try:
                if proc.is_running():
                    self.logger.debug(f"Sending SIGTERM to PID {proc.pid}")
                    proc.terminate()
            except psutil.NoSuchProcess:
                pass

        # Wait for graceful termination (1s of remaining budget)
        gone, alive = psutil.wait_procs(all_processes, timeout=1)

        # Phase 3: SIGKILL remaining processes
        if alive:
            self.logger.warning(f"{len(alive)} processes still running, force killing")
            for proc in alive:
                try:
                    self.logger.debug(f"Force killing PID {proc.pid}")
                    proc.kill()
                except psutil.NoSuchProcess:
                    pass

            # Final check (1s remaining)
            gone, still_alive = psutil.wait_procs(alive, timeout=1)
            if still_alive:
                self.logger.error(f"Failed to kill {len(still_alive)} processes")
                return False

        self.logger.info("Process tree terminated successfully")
        return True


class SystemdManager:
    """
    Handle systemd service interactions.

    Much cleaner than the bash systemd detection and management logic.
    """

    def __init__(self, logger: logging.Logger):
        self.logger = logger

    def is_running_under_systemd(self) -> bool:
        """Check if we're running under systemd"""
        return bool(os.getenv("INVOCATION_ID") or os.getenv("JOURNAL_STREAM"))

    def is_service_active(self, service_name: str) -> bool:
        """Check if systemd service is active"""
        try:
            result = subprocess.run(
                ["systemctl", "--user", "is-active", "--quiet", service_name],
                capture_output=True,
                timeout=10,
            )
            return result.returncode == 0
        except (subprocess.SubprocessError, FileNotFoundError):
            return False

    def stop_service(self, service_name: str) -> bool:
        """Stop systemd service"""
        if self.is_running_under_systemd():
            self.logger.info(
                "Running under systemd, skipping systemctl stop to avoid "
                "circular dependency"
            )
            return True

        if not self.is_service_active(service_name):
            self.logger.info(f"Service {service_name} is not active")
            return True

        try:
            self.logger.info(f"Stopping systemd service: {service_name}")
            result = subprocess.run(
                ["systemctl", "--user", "stop", service_name],
                capture_output=True,
                timeout=30,
                text=True,
            )

            if result.returncode == 0:
                self.logger.info(f"Successfully stopped {service_name}")
                time.sleep(2)  # Allow time for service to stop
                return True
            else:
                self.logger.error(f"Failed to stop {service_name}: {result.stderr}")
                return False

        except subprocess.TimeoutExpired:
            self.logger.error(f"Timeout stopping {service_name}")
            return False
        except FileNotFoundError:
            self.logger.warning("systemctl not found, skipping systemd service stop")
            return True


class GatewayServiceManager:
    """
    Main service manager class.

    Coordinates all the components to provide a reliable service lifecycle.
    """

    def __init__(self, environment: str = "default", fast_shutdown: bool = False):
        self.environment = environment
        self.config = GatewayConfig.from_environment(environment)

        # Override fast_shutdown if specified via constructor
        if fast_shutdown:
            self.config.fast_shutdown = fast_shutdown

        self.logger = self._setup_logging()
        self.process_manager = ProcessManager(self.logger)
        self.systemd_manager = SystemdManager(self.logger)

        # Runtime state
        self.gateway_process: psutil.Process | None = None
        self.cleanup_in_progress = False

        # Register cleanup handler
        atexit.register(self._cleanup)

    def _setup_logging(self) -> logging.Logger:
        """Configure logging with universal_logging auto-initialization"""
        # Set SERVICE_NAME for universal_logging
        os.environ["SERVICE_NAME"] = "_universal-llm-gateway"

        # Use universal_logging for proper configuration
        logger = get_logger("gateway-manager")
        return logger

    def _validate_configuration(self) -> None:
        """Validate configuration and exit on errors"""
        errors = self.config.validate()
        if errors:
            self.logger.error("Configuration validation failed:")
            for error in errors:
                self.logger.error(f"  - {error}")
            sys.exit(1)

    def _setup_environment(self) -> None:
        """Set up environment variables for the gateway process"""
        libs_dir = str(Path(self.config.project_root) / "libs")
        pythonpath_parts = [libs_dir, self.config.workdir]

        pythonpath_from_env = os.environ.get("PYTHONPATH", "")
        if pythonpath_from_env:
            for path in pythonpath_from_env.split(":"):
                if path and path not in pythonpath_parts:
                    pythonpath_parts.append(path)

        # Set all environment variables
        # Note: PATH is set to the virtual environment bin directory and the
        # existing PATH for subprocesses
        env_vars = {
            "PATH": f"{Path(self.config.gateway_venv) / 'bin'}:"
            f"{os.environ.get('PATH', '')}",
            "PYTHONPATH": ":".join(pythonpath_parts),
            "PYTHONUNBUFFERED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            # Gateway configuration
            "GATEWAY_HOST": self.config.host,
            "GATEWAY_PORT": str(self.config.port),
            "GATEWAY_API_KEY": self.config.gateway_api_key,
            "LOG_LEVEL": self.config.log_level,
            # Resource configuration
            "METRICS_MESSAGE_RETENTION": str(self.config.metrics_message_retention),
            "METRICS_ERROR_RETENTION": str(self.config.metrics_error_retention),
            "METRICS_QUEUE_SIZE": str(self.config.metrics_queue_size),
            "METRICS_QUEUE_TIMEOUT": str(self.config.metrics_queue_timeout),
            # CUDA configuration
            "CUDA_VISIBLE_DEVICES": self.config.cuda_visible_devices,
            "CUDA_HOME": self.config.cuda_home,
            "TORCH_CUDA_ARCH_LIST": self.config.torch_cuda_arch_list,
            # Cache and storage
            "MODEL_CACHE_DIR": self.config.model_cache_dir,
            # Process configuration
            "WORKER_LOG_DIR": self.config.worker_log_dir,
            "PROCESS_STARTUP_TIMEOUT": str(self.config.process_startup_timeout),
            "IPC_SOCKET_BASE_DIR": "/tmp/universal-protocol",
            # Feature flags
            "DEBUG_MODE": str(self.config.debug_mode).lower(),
            "ENABLE_PROFILING": str(self.config.enable_profiling).lower(),
            "ENABLE_MANAGEMENT_API": str(self.config.enable_management_api).lower(),
            "DISABLE_HEALTH_CHECKING": str(self.config.disable_health_checking).lower(),
            "DISABLE_UVICORN_ACCESS_LOG": str(
                self.config.disable_uvicorn_access_log
            ).lower(),
            "ENABLE_MODEL_AVAILABILITY_CHECK": str(
                self.config.enable_model_availability_check
            ).lower(),
            "FAST_SHUTDOWN": str(self.config.fast_shutdown).lower(),
        }

        # Log directory (used by logging.yaml)
        # Respect environment LOG_DIR (containers: /golem/logs/gateway)
        log_dir = os.getenv("LOG_DIR")
        if not log_dir:
            data_dir = os.getenv("DATA_DIR", "/tmp")
            log_dir = os.path.join(data_dir, "logs", "universal-llm-gateway")
        env_vars["LOG_DIR"] = log_dir
        env_vars["GATEWAY_LOG_DIR"] = log_dir  # Kept for compatibility
        # MODEL_PATH_ROOT: Only set if explicitly provided in environment
        # Avoids hardcoding deployment-specific paths
        if "MODEL_PATH_ROOT" in os.environ:
            env_vars["MODEL_PATH_ROOT"] = os.environ["MODEL_PATH_ROOT"]

        # CPU optimization (platform-specific - only set if explicitly provided)
        # Let libraries auto-detect optimal values if not set
        if self.config.omp_num_threads is not None:
            env_vars["OMP_NUM_THREADS"] = str(self.config.omp_num_threads)
        if self.config.mkl_num_threads is not None:
            env_vars["MKL_NUM_THREADS"] = str(self.config.mkl_num_threads)
        if self.config.tokenizers_parallelism is not None:
            env_vars["TOKENIZERS_PARALLELISM"] = str(
                self.config.tokenizers_parallelism
            ).lower()

        # Update environment
        for key, value in env_vars.items():
            os.environ[key] = value

        # Create necessary directories (respect LOG_DIR from environment)
        log_dir = env_vars.get("LOG_DIR", "/tmp/logs/universal-llm-gateway")
        directories_to_create = [
            "/tmp/universal-protocol",
            self.config.worker_log_dir,
            "/mnt/torus/.cache/huggingface/transformers",
            log_dir,
        ]

        for directory in directories_to_create:
            Path(directory).mkdir(parents=True, exist_ok=True)

        # Clean up worker logs from previous runs
        self._cleanup_worker_logs()

        self.logger.info("Environment configured successfully")

    def _cleanup_unix_socket(self, socket_path: str) -> None:
        """Remove stale Unix socket file if it exists."""
        import stat

        path = Path(socket_path)
        if path.exists():
            try:
                # Only remove if it's a socket file
                if stat.S_ISSOCK(path.stat().st_mode):
                    path.unlink()
                    self.logger.debug(f"Removed stale Unix socket: {socket_path}")
            except OSError as e:
                self.logger.warning(f"Could not remove stale Unix socket: {e}")

    def _ensure_socket_directory(self, socket_path: str) -> None:
        """Ensure the directory for the Unix socket exists."""
        socket_dir = Path(socket_path).parent
        socket_dir.mkdir(parents=True, exist_ok=True)
        self.logger.debug(f"Ensured socket directory exists: {socket_dir}")

    def _cleanup_worker_logs(self) -> None:
        """Clean up worker logs from previous gateway runs"""
        worker_logs_dir = Path(self.config.worker_log_dir)
        if not worker_logs_dir.exists():
            self.logger.debug(
                f"Worker logs directory does not exist: {worker_logs_dir}"
            )
            return

        try:
            log_files = list(worker_logs_dir.glob("*.log"))
            if log_files:
                self.logger.info(
                    f"Cleaning up {len(log_files)} worker log files from "
                    f"{worker_logs_dir}..."
                )
                removed_count = 0
                for log_file in log_files:
                    try:
                        self.logger.debug(f"Removing worker log: {log_file.name}")
                        log_file.unlink()
                        removed_count += 1
                    except Exception as e:
                        self.logger.warning(
                            f"Failed to remove worker log {log_file.name}: {e}"
                        )
                self.logger.info(
                    f"Worker log cleanup complete: removed "
                    f"{removed_count}/{len(log_files)} files"
                )
            else:
                self.logger.debug("No worker log files to clean up")
        except Exception as e:
            self.logger.warning(f"Error during worker log cleanup: {e}")

    def _stop_existing_services(self) -> None:
        """Stop existing gateway services and processes"""
        self.logger.info("Stopping existing gateway services...")

        # Stop systemd service
        self.systemd_manager.stop_service("super-universal-llm-gateway")

        # Find and stop existing processes (excluding ourselves)
        gateway_processes = self.process_manager.find_gateway_processes(
            port=self.config.port if not self.config.unix_socket else None,
            unix_socket=self.config.unix_socket,
        )
        port_processes = []
        if not self.config.unix_socket:
            port_processes = self.process_manager.find_processes_on_port(
                self.config.port
            )

        all_processes = list(set(gateway_processes + port_processes))

        # Filter out our own PID to prevent self-termination
        current_pid = os.getpid()
        all_processes = [proc for proc in all_processes if proc.pid != current_pid]

        if all_processes:
            self.logger.info(f"Found {len(all_processes)} existing processes to stop")
            for proc in all_processes:
                self.process_manager.terminate_process_tree(proc)

        # Clean up PID file
        pid_file = Path(self.config.pid_file)
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                if psutil.pid_exists(pid):
                    proc = psutil.Process(pid)
                    if proc.is_running():
                        self.process_manager.terminate_process_tree(proc)
            except (ValueError, psutil.NoSuchProcess, FileNotFoundError):
                # File might have been deleted by terminating process
                pass

            # Use unlink(missing_ok=True) to avoid race condition
            pid_file.unlink(missing_ok=True)
            self.logger.debug(f"Cleaned up PID file: {pid_file}")

        # Clean up Unix socket if configured
        if self.config.unix_socket:
            self._cleanup_unix_socket(self.config.unix_socket)

        # Clean up sockets
        socket_patterns = ["/tmp/universal-protocol/*.sock", "/tmp/process_ipc/*.sock"]

        for pattern in socket_patterns:
            import glob

            for socket_file in glob.glob(pattern):
                try:
                    Path(socket_file).unlink()
                    self.logger.debug(f"Removed socket: {socket_file}")
                except FileNotFoundError:
                    pass

    def _setup_signal_handlers(self) -> None:
        """Set up signal handlers for shutdown"""

        def signal_handler(signum, frame):
            if not self.cleanup_in_progress:
                if self.config.fast_shutdown:
                    self.logger.info(
                        f"Received signal {signum}, fast shutdown enabled - "
                        f"terminating immediately..."
                    )
                    self._fast_cleanup()
                else:
                    self.logger.info(
                        f"Received signal {signum}, initiating graceful shutdown..."
                    )
                    self._cleanup()
                sys.exit(0)

        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)

    def _start_uvicorn(self) -> None:
        """Start the uvicorn server process"""
        # Set up Unix socket if configured
        if self.config.unix_socket:
            self._ensure_socket_directory(self.config.unix_socket)
            self._cleanup_unix_socket(self.config.unix_socket)

        # Write our PID to the PID file
        with open(self.config.pid_file, "w") as f:
            f.write(str(os.getpid()))

        self.logger.info(f"Manager PID {os.getpid()} written to {self.config.pid_file}")

        # Build uvicorn command (logging setup is automatic)
        cmd = [
            self.config.python_executable,
            "-m",
            "uvicorn",
            "src.main:app",
            "--no-access-log",  # Disable uvicorn's access logging
        ]

        if self.config.unix_socket:
            # Unix socket mode
            cmd.extend(["--uds", self.config.unix_socket])
            self.logger.info(
                f"Starting uvicorn on Unix socket: {self.config.unix_socket}"
            )
        else:
            # TCP mode
            cmd.extend(["--host", self.config.host, "--port", str(self.config.port)])
            self.logger.info(
                f"Starting uvicorn on TCP: {self.config.host}:{self.config.port}"
            )

        self.logger.info(f"Starting uvicorn: {' '.join(cmd)}")

        # Start uvicorn process
        try:
            process = subprocess.Popen(
                cmd, cwd=self.config.workdir, env=os.environ.copy()
            )

            # Wrap in psutil for better management
            self.gateway_process = psutil.Process(process.pid)

            self.logger.info(f"Gateway started with PID: {self.gateway_process.pid}")

            # Wait for process to complete
            return_code = process.wait()
            self.logger.info(f"Gateway process exited with code: {return_code}")

        except Exception as e:
            self.logger.error(f"Failed to start gateway: {e}")
            raise

    def _fast_cleanup(self) -> None:
        """Clean up resources with immediate termination (development mode)."""
        if self.cleanup_in_progress:
            return

        self.cleanup_in_progress = True
        self.logger.info("Fast cleanup mode - immediate termination...")

        try:
            # Immediate SIGKILL - no graceful shutdown
            if self.gateway_process and self.gateway_process.is_running():
                self.logger.info("Force killing gateway process...")
                try:
                    # Kill process tree immediately
                    for child in self.gateway_process.children(recursive=True):
                        child.kill()
                    self.gateway_process.kill()
                except psutil.NoSuchProcess:
                    pass

            # Clean up any orphaned processes with immediate termination
            remaining_processes = self.process_manager.find_gateway_processes(
                port=self.config.port if not self.config.unix_socket else None,
                unix_socket=self.config.unix_socket,
            )
            current_pid = os.getpid()
            remaining_processes = [
                proc for proc in remaining_processes if proc.pid != current_pid
            ]

            if remaining_processes:
                self.logger.info(
                    f"Force killing {len(remaining_processes)} orphaned processes"
                )
                for proc in remaining_processes:
                    try:
                        for child in proc.children(recursive=True):
                            child.kill()
                        proc.kill()
                    except psutil.NoSuchProcess:
                        pass

            # Remove PID file
            pid_file = Path(self.config.pid_file)
            if pid_file.exists():
                try:
                    pid = int(pid_file.read_text().strip())
                    if pid == os.getpid():
                        pid_file.unlink(missing_ok=True)
                        self.logger.debug("Removed PID file")
                except (ValueError, FileNotFoundError):
                    pass

            # Clean up Unix socket
            if self.config.unix_socket:
                self._cleanup_unix_socket(self.config.unix_socket)

        except Exception as e:
            self.logger.error(f"Error during fast cleanup: {e}")
        finally:
            self.cleanup_in_progress = False

    def _cleanup(self) -> None:
        """Clean up resources and stop processes (graceful shutdown)."""
        if self.cleanup_in_progress:
            return

        self.cleanup_in_progress = True
        self.logger.info("Starting graceful cleanup...")

        try:
            # Graceful termination: SIGINT → SIGTERM → SIGKILL (3s total)
            if self.gateway_process and self.gateway_process.is_running():
                self.logger.info("Stopping gateway process...")
                self.process_manager.terminate_process_tree(
                    self.gateway_process, timeout=3, sigint_first=True
                )

            # Clean up any orphaned processes (excluding ourselves)
            remaining_processes = self.process_manager.find_gateway_processes(
                port=self.config.port if not self.config.unix_socket else None,
                unix_socket=self.config.unix_socket,
            )
            current_pid = os.getpid()
            remaining_processes = [
                proc for proc in remaining_processes if proc.pid != current_pid
            ]

            if remaining_processes:
                self.logger.info(
                    f"Cleaning up {len(remaining_processes)} orphaned processes"
                )
                for proc in remaining_processes:
                    self.process_manager.terminate_process_tree(proc, timeout=1)

            # Remove PID file
            pid_file = Path(self.config.pid_file)
            if pid_file.exists():
                try:
                    pid = int(pid_file.read_text().strip())
                    if pid == os.getpid():
                        pid_file.unlink(missing_ok=True)
                        self.logger.debug("Removed PID file")
                except (ValueError, FileNotFoundError):
                    pass

            # Clean up Unix socket
            if self.config.unix_socket:
                self._cleanup_unix_socket(self.config.unix_socket)

        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")
        finally:
            self.cleanup_in_progress = False

    def start(self) -> None:
        """
        Start the gateway service.

        Main entry point that coordinates the entire startup process.
        """
        try:
            self.logger.info("Starting Universal LLM Gateway Service Manager")
            self.logger.info(f"Environment: {self.environment}")
            if self.config.unix_socket:
                self.logger.info(f"Unix socket: {self.config.unix_socket}")
            else:
                self.logger.info(f"Host: {self.config.host}")
                self.logger.info(f"Port: {self.config.port}")
            self.logger.info(f"Working directory: {self.config.workdir}")
            self.logger.info(f"Virtual environment: {self.config.gateway_venv}")

            # Validate configuration
            self._validate_configuration()

            # Set up signal handlers
            self._setup_signal_handlers()

            # Stop existing services
            self._stop_existing_services()

            # Set up environment
            self._setup_environment()

            # Change to working directory
            os.chdir(self.config.workdir)

            # Start uvicorn
            self._start_uvicorn()

        except KeyboardInterrupt:
            self.logger.info("Received interrupt, shutting down...")
        except Exception as e:
            self.logger.error(f"Fatal error: {e}")
            raise
        finally:
            if self.config.fast_shutdown:
                self._fast_cleanup()
            else:
                self._cleanup()


def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Universal LLM Gateway Service Manager"
    )
    parser.add_argument(
        "--environment",
        "-e",
        default="default",
        help="Environment name (loads gateway-{env}.env file)",
    )
    parser.add_argument(
        "--fast-shutdown",
        action="store_true",
        help="Enable fast shutdown (immediate SIGKILL) for development",
    )
    args = parser.parse_args()

    # Create and start service manager
    try:
        manager = GatewayServiceManager(args.environment, args.fast_shutdown)
        manager.start()
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

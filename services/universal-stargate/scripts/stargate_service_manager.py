#!/usr/bin/env python3
"""
Universal Stargate Service Manager

Modern Python-based service manager replacing the complex bash script.
Provides robust process management, clean configuration, and reliable lifecycle.
"""

import atexit
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import psutil

# Repo root — derived from this file's position in the tree.
# {repo}/services/universal-stargate/scripts/stargate_service_manager.py
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent.parent)

# Early path setup for universal_logging import
_libs_path = Path(_PROJECT_ROOT) / "libs"
if _libs_path.exists() and str(_libs_path) not in sys.path:
    sys.path.insert(0, str(_libs_path))

# Note: Environment loading is handled by wrapper script (start-stargate.sh)
# which sources .env.local from project root before starting this Python service manager.
# All config is read from os.environ.

# CRITICAL: Set LOG_DIR and SERVICE_NAME before universal_logging import
# This prevents universal_logging from auto-initializing with wrong directory/filename
# Container environments set LOG_DIR via entrypoint (e.g., /golem/logs/stargate)
if not os.getenv("LOG_DIR"):
    data_dir = os.getenv("DATA_DIR", "/tmp")
    log_dir = os.path.join(data_dir, "logs", "universal-stargate")
    os.environ["LOG_DIR"] = log_dir
    Path(log_dir).mkdir(parents=True, exist_ok=True)

# Set SERVICE_NAME to match logging.yaml filename convention (underscore not hyphen)
# Without this, auto-initialization detects "universal-stargate" from directory name
# and creates empty "universal-stargate.log" instead of "universal_stargate.log"
if not os.getenv("SERVICE_NAME"):
    os.environ["SERVICE_NAME"] = "universal_stargate"

# Now safe to import universal_logging (will use correct LOG_DIR and SERVICE_NAME)
from universal_logging import get_logger  # noqa: E402
from universal_logging.utc_formatter import UTCFormatter  # noqa: E402


@dataclass
class StargateConfig:
    """
    Stargate configuration with validation and defaults.

    Configuration is loaded from environment variables only.
    The wrapper script (start-stargate.sh) sources .env.local from the project root
    before starting this service manager.
    """

    # Core service configuration
    host: str = "0.0.0.0"
    port: int = 9999
    log_level: str = "debug"

    # Gateway connection
    gateway_url: str = "http://localhost:9998"

    # Path configuration
    stargate_venv: str = field(
        default_factory=lambda: os.path.expanduser("~/.venvs/universal")
    )
    workdir: str = ""
    project_root: str = ""

    # Process configuration
    workers: int = 1
    limit_concurrency: int | None = None
    shutdown_grace: int = 20

    # Feature flags
    debug_mode: bool = True
    enable_tcp_monitoring: bool = False

    @classmethod
    def from_environment(cls, environment: str = "default") -> "StargateConfig":
        """
        Load configuration from environment variables.

        Environment variables are loaded by wrapper script (start-stargate.sh)
        which sources .env.local from the project root.

        Args:
            environment: Environment name (default, debug, release)

        Returns:
            Configured StargateConfig instance
        """

        config = cls(
            host=os.getenv("STARGATE_HOST", "0.0.0.0"),
            port=int(os.getenv("STARGATE_PORT", "9999")),
            log_level=os.getenv("STARGATE_LOG_LEVEL", "debug").lower(),
            gateway_url=os.getenv("GATEWAY_URL", "http://localhost:9998"),
            stargate_venv=os.getenv(
                "GATEWAY_VENV", os.path.expanduser("~/.venvs/universal")
            ),
            workers=int(os.getenv("STARGATE_WORKERS", "1")),
            limit_concurrency=int(os.getenv("STARGATE_LIMIT_CONCURRENCY"))
            if os.getenv("STARGATE_LIMIT_CONCURRENCY")
            else None,
            shutdown_grace=int(os.getenv("STARGATE_SHUTDOWN_GRACE", "20")),
            debug_mode=os.getenv("DEBUG_MODE", "true").lower() == "true",
            enable_tcp_monitoring=os.getenv(
                "STARGATE_ENABLE_TCP_MONITORING", "false"
            ).lower()
            == "true",
            project_root=_PROJECT_ROOT,
        )

        config.workdir = os.getenv("STARGATE_WORKDIR") or str(
            Path(config.project_root) / "services" / "universal-stargate"
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
        python_exec = Path(self.stargate_venv) / "bin" / "python"
        if not python_exec.exists():
            errors.append(f"Python executable not found: {python_exec}")

        # Validate working directory
        workdir_path = Path(self.workdir)
        if not workdir_path.exists():
            errors.append(f"Working directory not found: {self.workdir}")

        # Validate main module
        main_module = workdir_path / "start_proxy.py"
        if not main_module.exists():
            errors.append(f"Main module not found: {main_module}")

        return errors

    @property
    def python_executable(self) -> str:
        """Get path to Python executable in virtual environment"""
        return str(Path(self.stargate_venv) / "bin" / "python")

    @property
    def pid_file(self) -> str:
        """Get PID file path"""
        return "/tmp/universal-stargate.pid"


class ProcessManager:
    """
    Robust process management using psutil.

    Handles process discovery, termination, and cleanup much more reliably
    than the complex bash string manipulation in the original script.
    """

    def __init__(self, logger):
        self.logger = logger

    def _is_process_in_docker(self, proc: psutil.Process) -> bool:
        """
        Check if a process is running inside a Docker container.

        Args:
            proc: Process to check

        Returns:
            True if process is inside Docker container, False otherwise
        """
        try:
            # Check if process cgroup contains 'docker' or 'containerd'
            cgroup_file = f"/proc/{proc.pid}/cgroup"
            if Path(cgroup_file).exists():
                with open(cgroup_file) as f:
                    cgroup_content = f.read()
                    if "docker" in cgroup_content or "containerd" in cgroup_content:
                        return True
        except (FileNotFoundError, PermissionError, psutil.NoSuchProcess):
            pass
        return False

    def find_stargate_processes(self, port: int) -> list[psutil.Process]:
        """
        Find all stargate-related processes (excluding those in Docker containers).

        Args:
            port: Stargate port to search for

        Returns:
            List of Process objects for stargate-related processes
        """
        processes = []
        current_pid = os.getpid()  # Exclude our own PID

        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                # Skip our own process to avoid self-termination
                if proc.pid == current_pid:
                    continue

                # Skip processes running inside Docker containers
                if self._is_process_in_docker(proc):
                    continue

                cmdline = " ".join(proc.info["cmdline"] or [])

                # Check for start_proxy.py processes
                if "start_proxy.py" in cmdline and f"--port {port}" in cmdline:
                    processes.append(proc)
                    self.logger.debug(f"Found stargate proxy process: PID {proc.pid}")

                # Check for uvicorn processes running stargate
                elif (
                    "uvicorn" in cmdline
                    and ("proxy.app:app" in cmdline or "start_proxy:app" in cmdline)
                    and f"--port {port}" in cmdline
                ):
                    processes.append(proc)
                    self.logger.debug(f"Found stargate uvicorn process: PID {proc.pid}")

                # Check for python processes in stargate directory (excluding manager scripts)
                elif (
                    "python" in cmdline
                    and ("universal-stargate" in cmdline or "stargate" in cmdline)
                    and "stargate_service_manager.py" not in cmdline
                ):
                    processes.append(proc)
                    self.logger.debug(f"Found stargate python process: PID {proc.pid}")

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
        self, process: psutil.Process, timeout: int = 10
    ) -> bool:
        """
        Terminate a process and all its children gracefully.

        Args:
            process: Root process to terminate
            timeout: Seconds to wait for graceful termination

        Returns:
            True if all processes terminated successfully
        """
        if not process.is_running():
            return True

        # Get all children first
        children = self.get_process_tree(process)
        all_processes = children + [process]

        self.logger.info(f"Terminating process tree: {len(all_processes)} processes")

        # Send SIGTERM to all processes
        for proc in all_processes:
            try:
                if proc.is_running():
                    self.logger.debug(f"Sending SIGTERM to PID {proc.pid}")
                    proc.terminate()
            except psutil.NoSuchProcess:
                pass

        # Wait for graceful termination
        gone, alive = psutil.wait_procs(all_processes, timeout=timeout)

        if alive:
            self.logger.warning(
                f"{len(alive)} processes still running after {timeout}s, force killing"
            )
            # Force kill remaining processes
            for proc in alive:
                try:
                    self.logger.debug(f"Force killing PID {proc.pid}")
                    proc.kill()
                except psutil.NoSuchProcess:
                    pass

            # Final check
            gone, still_alive = psutil.wait_procs(alive, timeout=5)
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

    def __init__(self, logger):
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
                "Running under systemd, skipping systemctl stop to avoid circular dependency"
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


class StargateServiceManager:
    """
    Main service manager class.

    Coordinates all the components to provide a reliable service lifecycle.
    """

    def __init__(self, environment: str = "default"):
        self.environment = environment
        self.config = StargateConfig.from_environment(environment)

        # LOG_DIR already set at module level before universal_logging import
        # This ensures all loggers use correct directory from the start
        self.logger = self._setup_logging()
        self.process_manager = ProcessManager(self.logger)
        self.systemd_manager = SystemdManager(self.logger)

        # Runtime state
        self.stargate_process: psutil.Process | None = None
        self.cleanup_in_progress = False

        # Register cleanup handler
        atexit.register(self._cleanup)

    def _setup_logging(self):
        """Configure logging with appropriate level and format"""
        import logging

        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(
            UTCFormatter(
                fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%SZ",
            )
        )
        logging.basicConfig(
            level=getattr(logging, self.config.log_level.upper()),
            handlers=[stream_handler],
            force=True,
        )

        logger = get_logger("stargate-manager")
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
        """Set up environment variables for the stargate process"""
        libs_dir = str(Path(self.config.project_root) / "libs")
        pythonpath_parts = [libs_dir, self.config.workdir]

        existing_pythonpath = os.environ.get("PYTHONPATH")
        if existing_pythonpath:
            pythonpath_parts.append(existing_pythonpath)

        # Determine log directory (respect environment or use DATA_DIR-based default)
        log_dir = os.getenv("LOG_DIR")
        if not log_dir:
            data_dir = os.getenv("DATA_DIR", "/tmp")
            log_dir = os.path.join(data_dir, "logs", "universal-stargate")

        # Set all environment variables
        env_vars = {
            "PATH": f"{Path(self.config.stargate_venv) / 'bin'}:{os.environ.get('PATH', '')}",
            "PYTHONPATH": ":".join(pythonpath_parts),
            "PYTHONUNBUFFERED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            # Stargate configuration
            "STARGATE_HOST": self.config.host,
            "STARGATE_PORT": str(self.config.port),
            "STARGATE_LOG_LEVEL": self.config.log_level,
            "LOG_LEVEL": self.config.log_level,
            # Gateway connection
            "GATEWAY_URL": self.config.gateway_url,
            "STARGATE_GATEWAY_URL": self.config.gateway_url,
            # Feature flags
            "DEBUG_MODE": str(self.config.debug_mode).lower(),
            "STARGATE_DEBUG_REQUEST_SNAPSHOTS": os.getenv(
                "STARGATE_DEBUG_REQUEST_SNAPSHOTS", "false"
            ),
            # Directories
            "DATA_DIR": os.getenv("DATA_DIR", "/tmp"),
            "LOG_DIR": log_dir,
            "PROXY_PORT": str(self.config.port),
            # Pipeline search_paths resolve relative to this.
            # Derived from __file__ — works regardless of cwd or clone path.
            "STARGATE_PROJECT_ROOT": os.getenv(
                "STARGATE_PROJECT_ROOT",
                _PROJECT_ROOT,
            ),
        }

        # Update environment
        for key, value in env_vars.items():
            os.environ[key] = value

        # Create necessary directories
        directories_to_create = [log_dir, "/tmp/stargate"]

        for directory in directories_to_create:
            Path(directory).mkdir(parents=True, exist_ok=True)

        self.logger.info("Environment configured successfully")

    def _stop_existing_services(self) -> None:
        """Stop existing stargate services and processes"""
        self.logger.info("Stopping existing stargate services...")

        # Stop systemd service
        self.systemd_manager.stop_service("super-universal-stargate")

        # Find and stop existing processes (excluding ourselves)
        stargate_processes = self.process_manager.find_stargate_processes(
            self.config.port
        )
        port_processes = self.process_manager.find_processes_on_port(self.config.port)

        all_processes = list(set(stargate_processes + port_processes))

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
            except (ValueError, psutil.NoSuchProcess, FileNotFoundError, OSError):
                # Handle all possible file and process errors gracefully
                pass

            try:
                pid_file.unlink()
                self.logger.info(f"Removed PID file: {pid_file}")
            except FileNotFoundError:
                # File already removed, that's fine
                pass

        # Clean up sockets and temporary files
        cleanup_paths = [
            "/tmp/stargate",
        ]

        for cleanup_path in cleanup_paths:
            path = Path(cleanup_path)
            if path.exists():
                if path.is_dir():
                    import shutil

                    shutil.rmtree(cleanup_path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
                self.logger.debug(f"Cleaned up: {cleanup_path}")

    def _setup_signal_handlers(self) -> None:
        """Set up signal handlers for graceful shutdown"""

        def signal_handler(signum, frame):
            if not self.cleanup_in_progress:
                self.logger.info(
                    f"Received signal {signum}, initiating graceful shutdown..."
                )
                self._cleanup()
                sys.exit(0)

        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)

    def _start_proxy(self) -> None:
        """Start the stargate proxy process"""
        # Write our PID to the PID file
        with open(self.config.pid_file, "w") as f:
            f.write(str(os.getpid()))

        self.logger.info(f"Manager PID {os.getpid()} written to {self.config.pid_file}")

        # Build proxy command
        cmd = [
            self.config.python_executable,
            "start_proxy.py",
        ]

        # Check for Unix socket mode
        unix_socket = os.getenv("STARGATE_UNIX_SOCKET")
        if unix_socket:
            cmd.extend(["--unix-socket", unix_socket])
            self.logger.info(f"Using Unix socket: {unix_socket}")
        else:
            # TCP mode
            cmd.extend(
                [
                    "--host",
                    self.config.host,
                    "--port",
                    str(self.config.port),
                ]
            )

        cmd.extend(
            [
                "--log-level",
                self.config.log_level,
            ]
        )

        # Add optional limit-concurrency if set
        if self.config.limit_concurrency is not None:
            cmd.extend(["--limit-concurrency", str(self.config.limit_concurrency)])

        # Add TCP monitoring flag if enabled
        if self.config.enable_tcp_monitoring:
            cmd.append("--enable-tcp-monitoring")
            self.logger.info("TCP monitoring on port 9997 will be enabled")

        self.logger.info(f"Starting stargate proxy: {' '.join(cmd)}")

        # Start proxy process
        try:
            process = subprocess.Popen(
                cmd, cwd=self.config.workdir, env=os.environ.copy()
            )

            # Wrap in psutil for better management
            self.stargate_process = psutil.Process(process.pid)

            self.logger.info(f"Stargate started with PID: {self.stargate_process.pid}")

            # Wait for process to complete
            return_code = process.wait()
            self.logger.info(f"Stargate process exited with code: {return_code}")

        except Exception as e:
            self.logger.error(f"Failed to start stargate: {e}")
            raise

    def _cleanup(self) -> None:
        """Clean up resources and stop processes"""
        if self.cleanup_in_progress:
            return

        self.cleanup_in_progress = True
        self.logger.info("Starting cleanup process...")

        try:
            # Stop stargate process gracefully
            if self.stargate_process and self.stargate_process.is_running():
                self.logger.info("Stopping stargate process gracefully...")
                self.process_manager.terminate_process_tree(
                    self.stargate_process, timeout=self.config.shutdown_grace
                )

            # Clean up any remaining processes (excluding ourselves)
            remaining_processes = self.process_manager.find_stargate_processes(
                self.config.port
            )
            # Filter out our own PID to prevent self-termination during cleanup
            current_pid = os.getpid()
            remaining_processes = [
                proc for proc in remaining_processes if proc.pid != current_pid
            ]

            if remaining_processes:
                self.logger.info(
                    f"Cleaning up {len(remaining_processes)} remaining processes"
                )
                for proc in remaining_processes:
                    self.process_manager.terminate_process_tree(proc)

            # Remove PID file
            pid_file = Path(self.config.pid_file)
            if pid_file.exists():
                try:
                    pid = int(pid_file.read_text().strip())
                    if pid == os.getpid():
                        pid_file.unlink()
                        self.logger.info("Removed PID file")
                except (ValueError, FileNotFoundError, OSError):
                    # Handle all possible file errors gracefully during cleanup
                    pass

        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")
        finally:
            self.cleanup_in_progress = False

    def start(self) -> None:
        """
        Start the stargate service.

        Main entry point that coordinates the entire startup process.
        """
        try:
            self.logger.info("Starting Universal Stargate Service Manager")
            self.logger.info(f"Environment: {self.environment}")
            self.logger.info(f"Host: {self.config.host}")
            self.logger.info(f"Port: {self.config.port}")
            self.logger.info(f"Working directory: {self.config.workdir}")
            self.logger.info(f"Virtual environment: {self.config.stargate_venv}")
            self.logger.info(f"Gateway URL: {self.config.gateway_url}")

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

            # Start proxy
            self._start_proxy()

        except KeyboardInterrupt:
            self.logger.info("Received interrupt, shutting down...")
        except Exception as e:
            self.logger.error(f"Fatal error: {e}")
            raise
        finally:
            self._cleanup()


def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(description="Universal Stargate Service Manager")
    parser.add_argument(
        "--environment",
        "-e",
        default="default",
        choices=["default", "debug", "release"],
        help="Environment name (loads stargate-{env}.env file)",
    )
    args = parser.parse_args()

    # Create and start service manager
    try:
        manager = StargateServiceManager(args.environment)
        manager.start()
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

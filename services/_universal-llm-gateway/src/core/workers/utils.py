"""
Worker utilities and helper functions.

This module contains utility functions used across the workers module
including logging setup, health checks, and process utilities.
"""

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from .entrypoint import WorkerEntrypoint
from typing import Any

import psutil

logger = get_logger(__name__)


# truncate_for_logging() removed - use format_json_for_log() from universal_logging.json_utils


def validate_worker_dependencies(python_executable: str) -> tuple[bool, list[str]]:
    """
    Validate that all required dependencies are available for worker processes.

    Args:
        python_executable: Python executable path to check dependencies with

    Returns:
        Tuple of (is_valid, list_of_missing_dependencies)
    """
    missing_deps = []

    # Required dependencies for worker processes
    required_deps = ["inference_djinn", "process_ipc", "universal_logging"]

    logger.info(f"🔍 Validating worker dependencies using {python_executable}")

    for dep in required_deps:
        try:
            # Use subprocess to check if module can be imported
            result = subprocess.run(
                [python_executable, "-c", f"import {dep}"],
                capture_output=True,
                text=True,
                timeout=10.0,
            )

            if result.returncode != 0:
                missing_deps.append(dep)
                logger.warning(f"⚠️ Missing dependency: {dep}")
                logger.warning(f"⚠️ Import error: {result.stderr.strip()}")
            else:
                logger.debug(f"✅ Dependency available: {dep}")

        except subprocess.TimeoutExpired:
            missing_deps.append(f"{dep} (timeout during import)")
            logger.warning(f"⚠️ Dependency import timeout: {dep}")
        except Exception as e:
            missing_deps.append(f"{dep} (check failed: {e})")
            logger.warning(f"⚠️ Dependency check failed for {dep}: {e}")

    is_valid = len(missing_deps) == 0

    if is_valid:
        logger.info("✅ All worker dependencies validated successfully")
    else:
        logger.error(f"❌ Missing worker dependencies: {missing_deps}")

    return is_valid, missing_deps


def capture_subprocess_output(
    command: list[str],
    env: dict[str, str],
    cwd: str | None = None,
    timeout: float = 30.0,
) -> tuple[int, str, str]:
    """
    Capture subprocess output for diagnostic purposes.

    Args:
        command: Command to execute
        env: Environment variables
        cwd: Working directory
        timeout: Timeout in seconds

    Returns:
        Tuple of (return_code, stdout, stderr)
    """
    try:
        logger.debug(f"🔍 Capturing output for command: {' '.join(command)}")

        result = subprocess.run(
            command, env=env, cwd=cwd, capture_output=True, text=True, timeout=timeout
        )

        return result.returncode, result.stdout, result.stderr

    except subprocess.TimeoutExpired:
        logger.error(f"❌ Subprocess timeout after {timeout}s")
        return -1, "", f"Process timed out after {timeout} seconds"
    except Exception as e:
        logger.error(f"❌ Subprocess execution failed: {e}")
        return -1, "", str(e)


def diagnose_worker_startup_failure(
    command: list[str], env: dict[str, str], cwd: str | None = None
) -> dict[str, Any]:
    """
    Diagnose worker startup failures by capturing subprocess output and analyzing errors.

    Args:
        command: Worker command that failed
        env: Environment variables used
        cwd: Working directory

    Returns:
        Dictionary with diagnostic information
    """
    logger.info("🔍 Diagnosing worker startup failure...")

    # Capture subprocess output
    return_code, stdout, stderr = capture_subprocess_output(command, env, cwd)

    diagnosis = {
        "return_code": return_code,
        "stdout": stdout,
        "stderr": stderr,
        "command": command,
        "python_executable": command[0] if command else None,
        "worker_script": command[1] if len(command) > 1 else None,
        "timestamp": datetime.now().isoformat(),
        "issues": [],
    }

    # Analyze common failure patterns
    combined_output = (stdout + "\n" + stderr).lower()

    # Check for import errors
    if "importerror" in combined_output or "modulenotfounderror" in combined_output:
        diagnosis["issues"].append("import_error")

        # Check for specific missing modules
        if "inference_djinn" in combined_output:
            diagnosis["issues"].append("missing_inference_djinn")
        if "process_ipc" in combined_output:
            diagnosis["issues"].append("missing_process_ipc")
        if "universal_logging" in combined_output:
            diagnosis["issues"].append("missing_universal_logging")

    # Check for permission errors
    if "permission denied" in combined_output or "eacces" in combined_output:
        diagnosis["issues"].append("permission_error")

    # Check for file not found errors
    if (
        "no such file or directory" in combined_output
        or "filenotfounderror" in combined_output
    ):
        diagnosis["issues"].append("file_not_found")

    # Check for Python version issues
    if "syntaxerror" in combined_output and "python" in combined_output:
        diagnosis["issues"].append("python_version_incompatibility")

    # Check for CUDA/GPU issues
    if "cuda" in combined_output and (
        "error" in combined_output or "failed" in combined_output
    ):
        diagnosis["issues"].append("cuda_error")

    # Check for memory issues
    if "memory" in combined_output and (
        "error" in combined_output or "failed" in combined_output
    ):
        diagnosis["issues"].append("memory_error")

    # Log the diagnosis
    logger.error("❌ Worker startup diagnosis:")
    logger.error(f"   Return code: {return_code}")
    logger.error(f"   Issues detected: {diagnosis['issues']}")
    logger.error(f"   Command: {' '.join(command)}")

    if stdout.strip():
        logger.error(f"   Stdout: {stdout.strip()}")
    if stderr.strip():
        logger.error(f"   Stderr: {stderr.strip()}")

    return diagnosis


def check_process_health(pid: int) -> bool:
    """
    Check if a process is healthy by verifying it exists and is responsive.

    Args:
        pid: Process ID to check

    Returns:
        True if process is healthy, False otherwise
    """
    try:
        if not psutil:
            return False

        if not psutil.pid_exists(pid):
            return False

        process = psutil.Process(pid)
        return process.is_running()

    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False
    except Exception:
        return False


def find_worker_processes(model_id: str) -> list:
    """
    Find worker processes for a specific model.

    Args:
        model_id: Model ID to search for

    Returns:
        List of process info dictionaries
    """
    processes = []

    try:
        if not psutil:
            return processes

        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmdline = proc.info["cmdline"]
                if cmdline and len(cmdline) > 2:
                    if (
                        cmdline[1].endswith("worker.py")
                        and len(cmdline) > 2
                        and cmdline[2] == model_id
                    ):
                        processes.append({"pid": proc.info["pid"], "cmdline": cmdline})
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    except Exception:
        pass

    return processes


def cleanup_socket_file(socket_path: str) -> bool:
    """
    Clean up a socket file.

    Args:
        socket_path: Path to socket file

    Returns:
        True if cleanup successful, False otherwise
    """
    try:
        socket_file = Path(socket_path)
        if socket_file.exists():
            socket_file.unlink()
            return True
        return True
    except Exception:
        return False


def get_python_executable(
    gateway_config: Any, explicit_python: str | None = None
) -> str:
    """
    Determine the Python executable to use for worker processes.

    Args:
        gateway_config: Gateway configuration object
        explicit_python: Explicit Python executable override

    Returns:
        Path to Python executable
    """

    if explicit_python:
        return explicit_python

    if gateway_config and hasattr(gateway_config, "worker_processes"):
        worker_config = gateway_config.worker_processes
        if worker_config.use_inference_djinn_venv:
            djinn_venv_python = worker_config.inference_djinn_venv_path
            if Path(djinn_venv_python).exists():
                return djinn_venv_python

    return sys.executable


def create_worker_environment(model_id: str, python_executable: str) -> dict[str, str]:
    """
    Create environment variables for worker processes.

    **Environment Inheritance Flow**:
    1. Docker Compose loads docker/compose/engine-optimizations.env
    2. Gateway service manager reads from os.environ
    3. This function copies os.environ to pass complete environment to workers
    4. Engine environment variables are inherited from Docker environment
    5. Workers (libs/inference_djinn) inherit all threading/GPU/engine configuration

    Workers automatically receive:
    - Engine config (VLLM_*, TORCH_COMPILE_DISABLE, etc.) from Docker env files
    - Threading config (OMP_NUM_THREADS, MKL_NUM_THREADS, etc.)
    - GPU config (CUDA_VISIBLE_DEVICES, etc.)
    - All environment variables from Docker Compose

    NO dotenv loading in Python - Docker Compose handles all env file loading.

    Handles GPU access differently for CPU-only vs GPU models:
    - CPU models (ending with -cpu): Disable GPU access by setting CUDA_VISIBLE_DEVICES
      and HIP_VISIBLE_DEVICES to empty strings to prevent llama-cpp-python from detecting
      and initializing GPUs, ensuring no VRAM is used even when n_gpu_layers=0.
    - GPU models: Ensure GPU access is enabled by setting CUDA_VISIBLE_DEVICES to "0" if
      it's not already set or is empty. This ensures GPU layers can be offloaded.

    Args:
        model_id: Model ID for the worker (may include -cpu suffix for CPU-only models)
        python_executable: Python executable path

    Returns:
        Environment variables dictionary with gateway env + worker-specific vars
    """
    # Inherit complete environment from gateway (includes vars from Docker)
    env = os.environ.copy()

    # DEPRECATED: Engine env loading removed (engine_env.yaml deleted)
    # All engine variables now come from Docker Compose
    # Workers inherit VLLM_*, TORCH_COMPILE_DISABLE, etc. from os.environ

    env.update(
        {
            "DJINN_VENV_PATH": str(Path(python_executable).parent.parent),
            "MODEL_ID": model_id,
            "WORKER_ID": model_id,
        }
    )

    # Handle GPU visibility based on model type
    if model_id.endswith("-cpu"):
        # CPU models: Disable GPU visibility to prevent CUDA initialization
        # Empty CUDA_VISIBLE_DEVICES and HIP_VISIBLE_DEVICES to hide all GPUs
        # This prevents llama-cpp-python from detecting GPUs at startup
        env["CUDA_VISIBLE_DEVICES"] = ""
        env["HIP_VISIBLE_DEVICES"] = ""
    else:
        # GPU models: Ensure GPU access is enabled
        # If CUDA_VISIBLE_DEVICES is not set or is empty, default to "0"
        # This ensures the worker can see and use GPU 0 for layer offloading
        if not env.get("CUDA_VISIBLE_DEVICES"):
            env["CUDA_VISIBLE_DEVICES"] = "0"
        # HIP (AMD) - similarly ensure it's set if not already
        if not env.get("HIP_VISIBLE_DEVICES"):
            env["HIP_VISIBLE_DEVICES"] = "0"

    return env


def format_worker_command(
    python_executable: str,
    entrypoint: "WorkerEntrypoint",
    model_id: str,
    socket_path: str,
    log_file: str,
    log_level: str = "DEBUG",
    idle_timeout: float | None = None,
) -> list:
    """
    Format the command for starting a worker process.

    Args:
        python_executable: Python executable path
        entrypoint: Worker entrypoint specification (module or script)
        model_id: Model ID
        socket_path: Socket file path
        log_file: Log file path
        log_level: Logging level
        idle_timeout: Stream idle timeout in seconds (optional)

    Returns:
        Command list for subprocess
    """
    base_args = [
        model_id,  # worker_id
        model_id,  # model_id
        "--log-level",
        log_level,
        "--socket-path",
        socket_path,
        "--log-file",
        log_file,
    ]

    # Add idle timeout if provided
    if idle_timeout is not None:
        base_args.extend(["--idle-timeout", str(idle_timeout)])

    if entrypoint.kind == "module":
        return [python_executable, "-m", entrypoint.value] + base_args
    else:  # script
        return [python_executable, entrypoint.value] + base_args


def get_universal_protocol_socket_path(model_id: str) -> str:
    """
    Get Universal Protocol socket path for a model.

    Per Universal Protocol Model A MVP (Section 1.1), workers bind ASGI server to:
    /tmp/universal-protocol/worker-{id}.sock

    Args:
        model_id: Canonical model ID (no `:N` instance suffix)

    Returns:
        Universal Protocol socket path for RPC communication
    """
    return f"/tmp/universal-protocol/worker-{model_id}.sock"


def is_model_active(status) -> bool:
    """
    Check if model is in an active state.

    Active states are those where the model should not be unloaded:
    - BUSY: Processing inference or token counting
    - LOADING: Being loaded into memory
    - UNLOADING: Being removed from memory

    Args:
        status: Model status (ModelStatus enum)

    Returns:
        True if model is in an active state
    """
    from src.core.resources import ModelStatus

    return status in (ModelStatus.BUSY, ModelStatus.LOADING, ModelStatus.UNLOADING)

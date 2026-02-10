#!/usr/bin/env python3
"""Main FastAPI application for Universal LLM Gateway (Phase 2 - Actual Inference)"""

import os
import sys


# ============================================================================
# Fix Docker CPU Priority (must be first, before any CPU-intensive imports)
# ============================================================================
# Some Docker hosts start containers with reduced priority (nice 12)
# With SYS_NICE capability, we restore normal priority for optimal performance
def _fix_docker_priority():
    """Restore normal CPU priority if running in Docker with elevated nice."""
    try:
        import ctypes
        import ctypes.util

        # Get current nice level
        current = os.nice(0)
        if current > 0:
            # Use setpriority syscall to set absolute priority to 0
            # PRIO_PROCESS = 0, pid = 0 means current process
            libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
            result = libc.setpriority(0, 0, 0)
            if result == 0:
                new_nice = os.nice(0)
                print(f"[CPU] Priority restored: nice {current} → {new_nice}")
    except (OSError, PermissionError, AttributeError):
        pass  # SYS_NICE capability not available or not needed


_fix_docker_priority()


# ============================================================================
# Setup Logging Environment (must be first, before any logger initialization)
# ============================================================================
# Set LOG_DIR before any imports to prevent universal_logging from auto-
# initializing with wrong paths during module imports
def _setup_logging_environment():
    """Configure logging directories before any imports."""
    from pathlib import Path

    # Get LOG_DIR from environment, or use DATA_DIR-based default
    log_dir = os.getenv("LOG_DIR")
    if not log_dir:
        data_dir = os.getenv("DATA_DIR", "/tmp")
        log_dir = os.path.join(data_dir, "logs", "universal-llm-gateway")

    # Create directory
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    # Set in environment for universal_logging to use during auto-init
    os.environ["LOG_DIR"] = log_dir
    if not os.environ.get("SERVICE_NAME"):
        os.environ["SERVICE_NAME"] = "_universal-llm-gateway"

    print(f"[LOGGING] Log directory configured: {log_dir}")


_setup_logging_environment()


# ============================================================================
# Load Engine Environment Variables (must be early, before vLLM imports)
# ============================================================================
# DEPRECATED: engine_env.yaml removed. Engine vars now from Docker env files.
# This function remains for backward compatibility but returns empty dict.
def _load_engine_environment():
    """Load engine environment variables from config before any imports."""
    try:
        # Add libs to path if not already there
        libs_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "libs")
        libs_abs = os.path.abspath(libs_path)
        if libs_abs not in sys.path:
            sys.path.insert(0, libs_abs)

        from inference_djinn.config.env_loader import load_engine_env

        applied = load_engine_env()
        if applied:
            print(f"[ENGINE] Loaded {len(applied)} engine environment variables")
            for key, value in applied.items():
                print(f"[ENGINE]   {key}={value}")
    except Exception as e:
        print(f"[ENGINE] Warning: Failed to load engine environment: {e}")


_load_engine_environment()

try:
    from .app.app_factory import create_app
except ImportError:
    # When running directly, use absolute import
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from src.app.app_factory import create_app


def get_app():
    """Application factory for uvicorn factory mode.

    This function creates and returns the FastAPI application instance.
    Used by uvicorn in factory mode to avoid import side effects.
    """
    return create_app()


# Create app instance for direct uvicorn usage
app = get_app()


def _cleanup_socket(socket_path: str) -> None:
    """Remove stale socket file if it exists."""
    import stat
    from pathlib import Path

    path = Path(socket_path)
    if path.exists():
        try:
            # Only remove if it's a socket file
            if stat.S_ISSOCK(path.stat().st_mode):
                path.unlink()
                print(f"[SOCKET] Removed stale socket: {socket_path}")
        except OSError as e:
            print(f"[SOCKET] Warning: Could not remove stale socket: {e}")


def _ensure_socket_directory(socket_path: str) -> None:
    """Ensure the directory for the socket exists."""
    from pathlib import Path

    socket_dir = Path(socket_path).parent
    socket_dir.mkdir(parents=True, exist_ok=True)


def _set_socket_permissions(socket_path: str, permissions: int = 0o600) -> None:
    """Set socket file permissions for security."""
    import os
    from pathlib import Path

    path = Path(socket_path)
    if path.exists():
        try:
            os.chmod(socket_path, permissions)
            print(f"[SOCKET] Set permissions to {oct(permissions)}: {socket_path}")
        except OSError as e:
            print(f"[SOCKET] Warning: Could not set socket permissions: {e}")


if __name__ == "__main__":
    import argparse
    import atexit

    import uvicorn

    parser = argparse.ArgumentParser(description="Universal LLM Gateway")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to (TCP mode)")
    parser.add_argument(
        "--port", type=int, default=9998, help="Port to bind to (TCP mode)"
    )
    parser.add_argument(
        "--unix-socket",
        type=str,
        default=None,
        help="Unix socket path (overrides --host/--port)",
    )
    parser.add_argument("--log-level", default="info", help="Log level")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")

    args = parser.parse_args()

    # Set LOG_LEVEL environment variable from command-line argument
    os.environ["LOG_LEVEL"] = args.log_level.upper()

    if args.unix_socket:
        # Unix socket mode
        _ensure_socket_directory(args.unix_socket)
        _cleanup_socket(args.unix_socket)

        # Register cleanup on exit
        atexit.register(_cleanup_socket, args.unix_socket)

        print(f"[GATEWAY] Starting on Unix socket: {args.unix_socket}")

        # Configure uvicorn for Unix socket
        uvicorn.run(
            get_app,
            factory=True,
            uds=args.unix_socket,
            reload=args.reload,
            log_level=args.log_level,
        )
    else:
        # TCP mode (legacy)
        print(f"[GATEWAY] Starting on TCP: {args.host}:{args.port}")

        uvicorn.run(
            get_app,
            factory=True,
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_level=args.log_level,
        )

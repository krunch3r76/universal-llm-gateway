#!/usr/bin/env python3
"""
Standalone script to start the Universal LLM Gateway Stargate Proxy.
This script handles Python path setup automatically.
"""

import sys
from pathlib import Path

# Add the project root to Python path
project_root = Path(__file__).parent.absolute()
sys.path.insert(0, str(project_root))

# Clear any cached config module to avoid namespace conflicts
# (e.g., with universal_logging's config module)
import importlib

if "config" in sys.modules:
    del sys.modules["config"]
# Also clear any cached submodules
for key in list(sys.modules.keys()):
    if key.startswith("config."):
        del sys.modules[key]
importlib.invalidate_caches()

# MONKEY PATCH: Configure JSON to use unicode by default
import json

_original_dumps = json.dumps


def unicode_friendly_dumps(obj, **kwargs):
    """JSON dumps with ensure_ascii=False by default and ModelId support."""
    if "ensure_ascii" not in kwargs:
        kwargs["ensure_ascii"] = False

    # Add default serializer if not provided
    if "default" not in kwargs:

        def default_serializer(o):
            # Handle ModelId objects (duck-typing: has synthetic_id attribute)
            if hasattr(o, "synthetic_id"):
                return o.synthetic_id  # Preserves all flags for wire serialization
            # Let JSON encoder raise TypeError for other types
            raise TypeError(
                f"Object of type {o.__class__.__name__} is not JSON serializable"
            )

        kwargs["default"] = default_serializer

    return _original_dumps(obj, **kwargs)


json.dumps = unicode_friendly_dumps

# Configure logging with centralized configuration
from config.logging_config import get_domain_logger, load_logging_config

# Setup logging will be called in main() with proper log level
logger = None


# Global exception handler
def global_exception_handler(exc_type, exc_value, exc_traceback):
    """Global exception handler that logs with full traceback"""
    if issubclass(exc_type, KeyboardInterrupt):
        # Don't log keyboard interrupts
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    from universal_logging import get_logger

    logger = get_logger(__name__)
    logger.error(
        f"Uncaught exception: {exc_value}",
        exc_info=(exc_type, exc_value, exc_traceback),
    )


# Set the global exception handler
sys.excepthook = global_exception_handler


import argparse
import atexit
import stat

import uvicorn


def _cleanup_socket(socket_path: str) -> None:
    """Remove stale socket file if it exists."""
    path = Path(socket_path)
    if path.exists():
        try:
            if stat.S_ISSOCK(path.stat().st_mode):
                path.unlink()
                print(f"[SOCKET] Removed stale socket: {socket_path}")
        except OSError as e:
            print(f"[SOCKET] Warning: Could not remove stale socket: {e}")


def _ensure_socket_directory(socket_path: str) -> None:
    """Ensure the directory for the socket exists."""
    socket_dir = Path(socket_path).parent
    socket_dir.mkdir(parents=True, exist_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Universal LLM Gateway Stargate Proxy")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to (TCP mode)")
    parser.add_argument(
        "--port", type=int, default=9999, help="Port to bind to (TCP mode)"
    )
    parser.add_argument(
        "--unix-socket",
        type=str,
        default=None,
        help="Unix socket path (overrides --host/--port)",
    )
    parser.add_argument("--log-level", default="info", help="Log level")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    parser.add_argument(
        "--workers", type=int, default=1, help="Number of worker processes"
    )
    parser.add_argument(
        "--limit-concurrency", type=int, help="Maximum concurrent connections"
    )
    parser.add_argument(
        "--enable-tcp-monitoring",
        action="store_true",
        help="Enable TCP monitoring on port 9997 (default: Unix socket only)",
    )

    args = parser.parse_args()

    # Import os here for environment variable access
    import os

    # Set environment variable for TCP monitoring flag (read by StargateConfig)
    if args.enable_tcp_monitoring:
        os.environ["STARGATE_ENABLE_TCP_MONITORING"] = "1"

    # Setup logging FIRST, before any imports that might log
    global logger
    load_logging_config()

    # Import the app AFTER logging is configured
    try:
        from systems.proxy.app import app
    except ImportError as e:
        print(f"Failed to import stargate proxy app: {e}")
        sys.exit(1)

    # Root logger level is controlled by universal_logging configuration
    # Third-party library suppression is handled in logging.yaml
    import logging

    # Programmatically suppress noisy third-party loggers
    # (KEEP - third-party suppression)
    # This ensures suppression works even if logging.yaml fails to load
    noisy_loggers = [
        "httpcore",
        "httpcore.http11",
        "httpcore.connection",
        "httpcore._exceptions",
        "httpx",
        "urllib3",
        "urllib3.connectionpool",
        "requests",
        "aiohttp",
        "websockets",
        "websocket",
        "asyncio",
    ]
    for logger_name in noisy_loggers:
        third_party_logger = logging.getLogger(logger_name)
        third_party_logger.setLevel(logging.ERROR)
        third_party_logger.propagate = False

    logger = get_domain_logger()  # Root logger for proxy components

    # Configure uvicorn
    # Use separate env var for uvicorn internal logging (independent from app logging)
    uvicorn_log_level = os.getenv("UVICORN_LOG_LEVEL", "warning")
    uvicorn_config = {
        "log_level": uvicorn_log_level,
        "access_log": False,
        "log_config": None,
    }

    if args.unix_socket:
        # Unix socket mode
        _ensure_socket_directory(args.unix_socket)
        _cleanup_socket(args.unix_socket)
        atexit.register(_cleanup_socket, args.unix_socket)

        uvicorn_config["uds"] = args.unix_socket

        logger.info("Starting Universal LLM Gateway Stargate Proxy v2")
        logger.info(f"  project_root: {str(project_root)}")
        logger.info(f"  unix_socket: {args.unix_socket}")
        logger.info(f"  log_level: {args.log_level}")
        logger.info(f"  workers: {args.workers}")
    else:
        # TCP mode
        uvicorn_config["host"] = args.host
        uvicorn_config["port"] = args.port

        logger.info("Starting Universal LLM Gateway Stargate Proxy v2")
        logger.info(f"  project_root: {str(project_root)}")
        logger.info(f"  host: {args.host}")
        logger.info(f"  port: {args.port}")
        logger.info(f"  log_level: {args.log_level}")
        logger.info(f"  workers: {args.workers}")

    if args.limit_concurrency:
        uvicorn_config["limit_concurrency"] = args.limit_concurrency
        logger.info(f"  limit_concurrency: {args.limit_concurrency}")

    logger.info("Universal LLM Gateway Stargate Proxy v2 startup complete")

    try:
        if args.workers > 1:
            uvicorn_config["app"] = "systems.proxy.app:app"
            uvicorn_config["workers"] = args.workers
            if args.reload:
                logger.warning("Reload disabled when using multiple workers")
        else:
            uvicorn_config["app"] = app
            uvicorn_config["reload"] = args.reload

        uvicorn.run(**uvicorn_config)
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
    finally:
        logger.info("Shutting down Universal LLM Gateway Stargate Proxy")
        logger.info("Universal LLM Gateway Stargate Proxy shutdown complete")


if __name__ == "__main__":
    main()

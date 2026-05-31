#!/usr/bin/env python3
"""Start the Universal Protocol ASGI server.

Launches uvicorn with the Starlette ASGI app bound to a Unix domain socket.

Usage:
    python start_server.py --worker-id 1
    python start_server.py --worker-id 2

Environment:
    PYTHONPATH must include <project_root>/libs
    sitecustomize.py configures this automatically when run from the project root.

Socket Path:
    /tmp/universal-protocol/worker-{id}.sock
"""

import argparse
import sys
from pathlib import Path

from universal_logging import INFO, get_logger

# Configure logging before imports
logging.basicConfig(
    level=INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = get_logger(__name__)


def main() -> int:
    """Start the Universal Protocol server.

    Args:
        None (uses command-line arguments)

    Returns:
        Exit code (0 = success, 1 = error)
    """
    # Parse arguments
    parser = argparse.ArgumentParser(
        description="Start Universal Protocol ASGI server on Unix socket",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Start worker 1
  python start_server.py --worker-id 1
  
  # Start worker with custom socket directory (for testing)
  python start_server.py --worker-id 1 --socket-dir /tmp/test-sockets
  
  # Show help
  python start_server.py --help
        """,
    )

    parser.add_argument(
        "--worker-id",
        type=int,
        required=True,
        help="Worker ID (1-N). Determines socket path: /tmp/universal-protocol/worker-{id}.sock",
    )
    parser.add_argument(
        "--socket-dir",
        type=str,
        default="/tmp/universal-protocol",
        help="Directory for socket files (default: /tmp/universal-protocol)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="info",
        choices=["debug", "info", "warning", "error"],
        help="Logging level (default: info)",
    )

    args = parser.parse_args()

    # Validate worker ID
    if args.worker_id < 1:
        logger.error(f"Worker ID must be >= 1, got {args.worker_id}")
        return 1

    # Create socket directory if it doesn't exist
    socket_dir = Path(args.socket_dir)
    try:
        socket_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Socket directory: {socket_dir}")
    except OSError as e:
        logger.error(f"Failed to create socket directory {socket_dir}: {e}")
        return 1

    # Construct socket path
    socket_path = socket_dir / f"worker-{args.worker_id}.sock"
    logger.info(f"Socket path: {socket_path}")

    # Remove stale socket if it exists
    if socket_path.exists():
        try:
            socket_path.unlink()
            logger.info(f"Removed stale socket: {socket_path}")
        except OSError as e:
            logger.warning(f"Failed to remove stale socket {socket_path}: {e}")

    # Check if we can import uvicorn
    try:
        import uvicorn
    except ImportError:
        logger.error(
            "uvicorn not installed. Install with: pip install uvicorn[standard]"
        )
        return 1

    # Check if we can import the ASGI app and serve function
    try:
        from universal_protocol.server import serve
        from universal_protocol.server.asgi_app import app
    except ImportError as e:
        logger.error(f"Failed to import Universal Protocol modules: {e}")
        logger.error(
            "Ensure PYTHONPATH includes <project_root>/libs (sitecustomize.py handles this)"
        )
        return 1

    # Prepare uvicorn config
    logger.info(f"Starting Universal Protocol server on worker-{args.worker_id}")
    logger.info(f"Socket: {socket_path}")
    logger.info(f"Log level: {args.log_level}")

    # Run server using secure serve function
    try:
        import asyncio

        asyncio.run(
            serve(
                app=app,
                socket_path=str(socket_path),
                loop="uvloop",
                log_level=args.log_level.lower(),
            )
        )
    except KeyboardInterrupt:
        logger.info("Server interrupted by user")
        return 0
    except Exception as e:
        logger.exception(f"Server error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

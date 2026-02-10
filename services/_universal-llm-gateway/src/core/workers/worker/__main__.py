#!/usr/bin/env python3
"""
Worker process main entry point.

Handles argument parsing, logging setup, and worker lifecycle.

CRITICAL: Import order matters here! We must configure logging BEFORE importing
Worker or any module that uses universal_logging.get_logger() at module level.
Otherwise, SmartLogger auto-initialization will discover logging.yaml with
truncate_logs: true and truncate the gateway log file.
"""

import argparse
import asyncio
import faulthandler
import os
import sys
from pathlib import Path

# Enable faulthandler EARLY to catch segfaults in native code (e.g., llama.cpp)
# This prints a stack trace to stderr before the process terminates
faulthandler.enable()

# Add project root to Python path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

# Import from worker_logging module - it has NO module-level get_logger() calls
# so it won't trigger SmartLogger auto-initialization
from ..worker_logging import setup_worker_logging  # noqa: E402

# NOTE: Worker import is DEFERRED to main() after setup_worker_logging() runs
# to prevent SmartLogger auto-initialization from truncating gateway.log


async def main():
    """Main worker entry point."""
    parser = argparse.ArgumentParser(description="Simple LLM Worker")
    parser.add_argument("worker_id", help="Worker ID")
    parser.add_argument("model_id", help="Model ID")
    parser.add_argument("--log-level", default="INFO", help="Log level")
    parser.add_argument("--socket-path", required=True, help="Socket path")
    parser.add_argument("--log-file", help="Log file path")
    parser.add_argument(
        "--idle-timeout",
        type=float,
        default=None,
        help="Stream idle timeout in seconds (overrides env var and default)",
    )

    args = parser.parse_args()

    # Setup logging FIRST, before ANY imports that use get_logger()
    # This prevents SmartLogger auto-initialization from loading logging.yaml
    # which has truncate_logs: true and would truncate the gateway log file
    setup_worker_logging(args.log_level, args.log_file)

    # NOW safe to import Worker and get logger - logging is already configured
    from universal_logging import get_logger

    from . import Worker

    logger = get_logger(__name__)

    logger.info(f"🚀 Starting simple worker {args.worker_id} for model {args.model_id}")

    # Log environment configuration (debug level)
    logger.debug("🔧 [worker] Environment configuration:")

    # Log OpenMP/threading configuration
    thread_vars = {
        "OMP_NUM_THREADS": os.environ.get("OMP_NUM_THREADS", "not set"),
        "MKL_NUM_THREADS": os.environ.get("MKL_NUM_THREADS", "not set"),
        "OPENBLAS_NUM_THREADS": os.environ.get("OPENBLAS_NUM_THREADS", "not set"),
        "TOKENIZERS_PARALLELISM": os.environ.get("TOKENIZERS_PARALLELISM", "not set"),
    }
    logger.debug(f"🔧 [worker] Thread configuration: {thread_vars}")

    # Log GPU configuration
    gpu_vars = {
        "CUDA_VISIBLE_DEVICES": os.environ.get("CUDA_VISIBLE_DEVICES", "not set"),
        "HIP_VISIBLE_DEVICES": os.environ.get("HIP_VISIBLE_DEVICES", "not set"),
    }
    logger.debug(f"🔧 [worker] GPU configuration: {gpu_vars}")

    # Log full environment at DEBUG level (can be verbose)
    if args.log_level == "DEBUG":
        logger.debug("🔧 [worker] Full environment variables:")
        for key, value in sorted(os.environ.items()):
            # Mask sensitive values
            if any(
                sensitive in key.upper()
                for sensitive in ["TOKEN", "KEY", "SECRET", "PASSWORD"]
            ):
                logger.debug(f"   {key}=***MASKED***")
            else:
                logger.debug(f"   {key}={value}")

    # Create and run worker
    worker = Worker(
        args.worker_id, args.socket_path, args.model_id, idle_timeout=args.idle_timeout
    )

    try:
        # Initialize the worker (creates socket)
        logger.info(f"🔧 [worker] About to call worker.initialize({args.socket_path})")
        await worker.initialize(args.socket_path)
        logger.info("🔧 [worker] worker.initialize() completed successfully")

        # Run the worker (starts Universal Protocol server)
        logger.info("🔧 [worker] About to call worker.run()")
        await worker.run()
        logger.info("🔧 [worker] worker.run() completed")
    except KeyboardInterrupt:
        logger.info("🛑 Worker stopped by user")
    except Exception as e:
        logger.error(f"❌ Worker error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

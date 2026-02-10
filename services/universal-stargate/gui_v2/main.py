"""
Universal Stargate GUI v2 - Main Entry Point

A monitoring interface for Universal Stargate chat completions with
session management and real-time updates.
"""

import argparse
import sys

from universal_logging import get_logger

from config.logging_config import load_logging_config

from .controller import AppController


def main():
    """Main entry point."""
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Universal Stargate GUI v2")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.parse_args()

    # Setup logging using project configuration
    load_logging_config()
    logger = get_logger(__name__)

    try:
        # Create and start application
        logger.info("Starting Universal Stargate GUI v2")
        app = AppController()
        app.start()

    except KeyboardInterrupt:
        logger.info("Application terminated by user")
        sys.exit(0)

    except Exception as e:
        logger.error(f"Application error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Example demonstrating the auto-initialization feature.

NOTE: This example has been updated to demonstrate auto-initialization
instead of manual truncation. The new SmartLogger handles configuration
automatically.
"""

from universal_logging import get_logger


def main():
    """Demonstrate auto-initialization functionality."""

    print("=== Universal Logging Auto-Initialization Example ===\n")

    # Get logger - auto-initializes on first use
    print("1. Getting logger (triggers auto-initialization)")
    logger = get_logger("example")

    # Log some messages
    print("\n2. Logging messages (no setup required!)")
    logger.info("This is an info message")
    logger.warning("This is a warning message")
    logger.error("This is an error message")
    logger.debug("This is a debug message")

    # Get another logger with different name
    print("\n3. Getting another logger")
    logger2 = get_logger("example.submodule")
    logger2.info("Logger with hierarchical name")

    # Demonstrate context detection
    print("\n4. Context detection:")
    print("   - Service name: auto-detected")
    print("   - Log level: from LOG_LEVEL env var or default INFO")
    print("   - Log truncation: from TRUNCATE_LOGS env var or config file")
    print("   - Configuration: auto-discovered from ./config/ or default")

    # Show environment variable usage
    print("\n5. Environment variable control:")
    print("   Set these before running:")
    print("   - export LOG_LEVEL=DEBUG      # Control log level")
    print("   - export TRUNCATE_LOGS=true   # Enable log truncation")
    print("   - export LOG_DIR=/path/to/logs # Custom log directory")

    print("\n=== Example completed ===")
    print("✓ Zero-setup logging - just import and use get_logger()!")
    print("✓ Configuration auto-detected from environment and files")
    print("✓ No manual setup_logging() calls required")


if __name__ == "__main__":
    main()

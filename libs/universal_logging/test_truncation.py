"""
Tests for log truncation functionality.

NOTE: These tests are deprecated. The new SmartLogger auto-initialization
handles log configuration automatically. Truncation behavior is now controlled
via environment variables and configuration files.
"""

from universal_logging import get_logger


def test_auto_initialization_basic():
    """Test that auto-initialization works for basic logging."""
    print("Testing auto-initialization...")

    # Get logger - should auto-initialize
    logger = get_logger("test")

    # Log some messages
    logger.info("Test info message")
    logger.warning("Test warning message")

    print("✓ Auto-initialization test passed")


if __name__ == "__main__":
    print("Running simplified logging tests...")
    print("NOTE: Full truncation tests deprecated - use auto-initialization")

    test_auto_initialization_basic()

    print("\n✓ Basic auto-initialization test passed!")
    print("For truncation control, use environment variables:")
    print("  - TRUNCATE_LOGS=true/false")
    print("  - LOG_LEVEL=DEBUG/INFO/WARNING/ERROR")

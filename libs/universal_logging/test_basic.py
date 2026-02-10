#!/usr/bin/env python3
"""
Basic test script for universal_logging package.
"""

import os
import sys

# Add the package to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def test_basic_imports():
    """Test basic imports work."""
    print("Testing basic imports...")

    try:
        import universal_logging

        # Test that key attributes are available
        assert hasattr(universal_logging, "get_logger")
        assert hasattr(universal_logging, "SmartLogger")
        assert hasattr(universal_logging, "JSONFormatter")
        print("✓ Basic imports successful")
        return True
    except (ImportError, AssertionError) as e:
        print(f"❌ Import error: {e}")
        return False


def test_auto_initialization():
    """Test auto-initialization functionality."""
    print("Testing auto-initialization...")

    try:
        from universal_logging import get_logger

        # Get logger - should auto-initialize
        logger = get_logger("test")

        # Test basic logging
        logger.info("Test info message")
        logger.warning("Test warning message")
        logger.error("Test error message")

        print("✓ Auto-initialization tests passed")
        return True
    except Exception as e:
        print(f"❌ Auto-initialization test failed: {e}")
        return False


def test_smart_logger():
    """Test SmartLogger functionality."""
    print("Testing SmartLogger...")

    try:
        from universal_logging import SmartLogger

        # Create SmartLogger instance
        smart_logger = SmartLogger()

        # Get logger - should auto-configure
        logger = smart_logger.get_logger("test")

        # Test that logger is working
        assert logger is not None
        logger.debug("Debug message")

        print("✓ SmartLogger tests passed")
        return True
    except Exception as e:
        print(f"❌ SmartLogger test failed: {e}")
        return False


def test_json_formatter():
    """Test JSONFormatter functionality."""
    print("Testing JSONFormatter...")

    try:
        import json
        import logging

        from universal_logging import JSONFormatter

        # Create formatter
        formatter = JSONFormatter()

        # Create a log record
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=42,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        # Format the record
        formatted = formatter.format(record)

        # Parse JSON and check that it contains expected fields
        parsed = json.loads(formatted)
        assert parsed["logger"] == "test"
        assert parsed["level"] == "INFO"
        assert "Test message" in parsed["message"]

        print("✓ JSONFormatter tests passed")
        return True
    except Exception as e:
        print(f"❌ JSONFormatter test failed: {e}")
        return False


def main():
    """Run all tests."""
    print("🧪 Running universal_logging tests...\n")

    tests = [
        test_basic_imports,
        test_auto_initialization,
        test_smart_logger,
        test_json_formatter,
    ]

    passed = 0
    total = len(tests)

    for test in tests:
        if test():
            passed += 1
        print()

    print(f"📊 Test Results: {passed}/{total} tests passed")

    if passed == total:
        print(
            "🎉 All tests passed! The universal_logging package is working correctly."
        )
        return True
    else:
        print("❌ Some tests failed. Please check the errors above.")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)

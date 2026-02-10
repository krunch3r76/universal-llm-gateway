#!/usr/bin/env python3
"""
Test script to verify dictConfig can resolve universal_logging.renderers.JSONFormatter

This script reproduces and verifies the fix for:
    dictConfig failed: Unable to configure formatter 'json'
    AttributeError: module 'universal_logging' has no attribute 'renderers'

Root cause: The renderers submodule needed an eager import in universal_logging/__init__.py
Fix: Added `from . import renderers as renderers` similar to handlers import
"""

import logging.config
import os
import sys


def test_renderers_accessible():
    """Test that renderers module and JSONFormatter are accessible."""
    import universal_logging

    assert hasattr(
        universal_logging, "renderers"
    ), "universal_logging.renderers not accessible"
    assert hasattr(
        universal_logging.renderers, "JSONFormatter"
    ), "universal_logging.renderers.JSONFormatter not accessible"
    print("✓ Renderers module accessible")


def test_dictconfig_with_class_syntax():
    """Test dictConfig with 'class:' syntax."""
    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json": {
                "class": "universal_logging.renderers.JSONFormatter",
                "truncate": False,
            }
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "json",
                "level": "INFO",
                "stream": "ext://sys.stdout",
            }
        },
        "root": {"level": "INFO", "handlers": ["console"]},
    }

    try:
        logging.config.dictConfig(config)
        print("✓ dictConfig with 'class:' syntax succeeded")
        return True
    except Exception as e:
        print(f"✗ dictConfig failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def test_dictconfig_with_factory_syntax():
    """Test dictConfig with '():' factory syntax."""
    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json": {
                "()": "universal_logging.renderers.JSONFormatter",
                "truncate": True,
                "colors": False,
            }
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "json",
                "level": "INFO",
                "stream": "ext://sys.stdout",
            }
        },
        "root": {"level": "INFO", "handlers": ["console"]},
    }

    try:
        logging.config.dictConfig(config)
        print("✓ dictConfig with '():' factory syntax succeeded")
        return True
    except Exception as e:
        print(f"✗ dictConfig failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def test_logging_output():
    """Test that logging actually works."""
    from universal_logging import get_logger

    logger = get_logger("test_logging_config")
    logger.info("Test message from test_logging_config")
    print("✓ Logger output works")


def main():
    """Run all tests."""
    print("=" * 60)
    print("Testing universal_logging dictConfig compatibility")
    print("=" * 60)

    # Ensure libs is in path
    if "libs" not in sys.path:
        sys.path.insert(0, "libs")

    success = True

    try:
        test_renderers_accessible()
        success &= test_dictconfig_with_class_syntax()
        success &= test_dictconfig_with_factory_syntax()
        test_logging_output()
    except Exception as e:
        print(f"✗ Test suite failed: {e}")
        import traceback

        traceback.print_exc()
        return 1

    print("=" * 60)
    if success:
        print("✓ All tests passed")
        return 0
    else:
        print("✗ Some tests failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())

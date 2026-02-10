"""
Handler Verification

Runtime verification that configured handlers are working correctly.
"""

import logging
import os
from typing import TYPE_CHECKING

from universal_logging import INFO, WARNING, get_logger
from universal_logging.bootstrap import bootstrap_logger

if TYPE_CHECKING:
    from ..core import SmartLogger


def verify_handlers(smart_logger: "SmartLogger"):
    """
    Verify that all configured handlers are working.

    Controlled by:
    - UNIVERSAL_LOGGING_VERIFY_HANDLERS env var (explicit enable/disable)
    - Auto-enabled if UNIVERSAL_LOGGING_BOOTSTRAP >= DEBUG
    - Default: disabled in production (SILENT)

    Args:
        smart_logger: SmartLogger instance

    Invariant: ∀ call: (verify_enabled ∨ bootstrap.is_debug) ⟹ verification_runs
    """
    # Check if verification explicitly enabled/disabled
    verify_env = os.getenv("UNIVERSAL_LOGGING_VERIFY_HANDLERS")
    if verify_env is not None:
        should_verify = verify_env.lower() in ("1", "true", "yes")
    else:
        # Default: only verify if bootstrap debug enabled
        should_verify = bootstrap_logger.is_debug()

    if not should_verify:
        bootstrap_logger.debug(
            "Handler verification disabled "
            "(set UNIVERSAL_LOGGING_VERIFY_HANDLERS=1 to enable)"
        )
        return

    bootstrap_logger.info("Verifying handlers...")

    logger = get_logger("universal_logging.verification")
    handlers_to_test = list(logger.handlers)
    failed_handlers = []

    for handler in handlers_to_test:
        try:
            # Silent test - use empty message to avoid spam
            test_record = logging.LogRecord(
                name="universal_logging.test",
                level=INFO,
                pathname="",
                lineno=0,
                msg="",  # Empty message - no spam
                args=(),
                exc_info=None,
            )
            handler.emit(test_record)
            bootstrap_logger.debug(f"Handler {handler.__class__.__name__} verified")

        except Exception as e:
            smart_logger.metrics["warning_count"] += 1
            bootstrap_logger.error(f"Handler {handler.__class__.__name__} failed: {e}")
            failed_handlers.append(handler)

    # Remove failed handlers
    for handler in failed_handlers:
        logger.removeHandler(handler)
        bootstrap_logger.info(f"Removed failed handler: {handler.__class__.__name__}")

    # Add emergency console handler if no handlers remain
    if not logger.handlers:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(WARNING)
        logger.addHandler(console_handler)
        bootstrap_logger.info("Added emergency console handler")

    verified_count = len(handlers_to_test) - len(failed_handlers)
    bootstrap_logger.info(
        f"Handler verification complete: "
        f"{verified_count}/{len(handlers_to_test)} passed"
    )

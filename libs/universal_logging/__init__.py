"""
Universal Logging Module

A reusable logging module that provides enhanced logging capabilities
with automatic initialization, caller information, and flexible configuration.

Auto-Initializing API:
    # Zero-setup logging - just import and use
    from universal_logging import get_logger, ERROR
    logger = get_logger(__name__)
    logger.info("Hello world")

    # Or use the pre-configured convenience logger
    from universal_logging import get_convenience_logger
    logger = get_convenience_logger()
    logger.info("Quick logging")

Automatic Log Truncation:
    # Use setup() to apply config with automatic truncation support:
    from universal_logging import setup
    setup(config)  # Handles truncate_logs: true automatically

    # ⚠️ Standard dictConfig does NOT handle truncation:
    import logging.config
    logging.config.dictConfig(config)  # truncate_logs is IGNORED

Thread Safety: Uses per-event-loop storage via contextvars.
No threading locks needed - contextvars are inherently thread-safe.
"""

import asyncio
import weakref
from contextvars import ContextVar

# Re-export standard logging constants for convenience
# This allows: from universal_logging import get_logger, ERROR, WARNING
# Instead of: import logging; ERROR
from logging import (
    CRITICAL,
    DEBUG,
    ERROR,
    FATAL,  # Alias for CRITICAL
    INFO,
    NOTSET,
    WARNING,
)
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .bootstrap import BootstrapLevel, BootstrapLogger, bootstrap_logger
    from .smart_logger import SmartLogger

# Eager import: prevents __getattr__ recursion when dictConfig resolves
# "universal_logging.handlers.AutoFlushFileHandler"
from . import handlers as handlers  # noqa: F401

# Eager import: prevents __getattr__ recursion when dictConfig resolves
# "universal_logging.renderers.JSONFormatter"
from . import renderers as renderers  # noqa: F401

# Per-event-loop logger storage
# Key: event loop id (int), Value: SmartLogger instance
# Uses id(loop) because event loops aren't hashable
_logger_by_loop: dict[int, "SmartLogger"] = {}

# Weak references to event loops for lifecycle tracking
# Allows cleanup when loops are garbage collected
_loop_refs: dict[int, weakref.ref] = {}

# Context variable for fast path access
# Each async task inherits the logger from its parent context
_current_logger: ContextVar["SmartLogger | None"] = ContextVar(
    "current_logger", default=None
)


def _get_or_create_logger() -> "SmartLogger":
    """Get or create SmartLogger for current execution context.

    Returns:
        SmartLogger instance for the current event loop (async) or
        the fallback logger (sync/module-import context, loop_id=0).

    Thread Safety: No locks needed — contextvars are inherently thread-safe,
    and each event loop gets its own isolated instance.
    """
    # Fast path: check context variable
    logger_instance = _current_logger.get()
    if logger_instance is not None:
        return logger_instance

    # Determine context: async or sync
    try:
        loop = asyncio.get_running_loop()
        loop_id = id(loop)

        # Check if cached logger is for a stale (reused) id
        # Python can reuse id() values after objects are garbage collected
        if loop_id in _logger_by_loop:
            ref = _loop_refs.get(loop_id)
            if ref is None or ref() is None:
                # Stale entry - loop was garbage collected, id reused
                del _logger_by_loop[loop_id]
                if loop_id in _loop_refs:
                    del _loop_refs[loop_id]

    except RuntimeError:
        # Called outside async context (e.g., module imports)
        loop = None
        loop_id = 0

    # Check if logger exists for this loop
    if loop_id in _logger_by_loop:
        logger_instance = _logger_by_loop[loop_id]
    else:
        # Create new logger for this loop
        from .smart_logger import SmartLogger

        logger_instance = SmartLogger()
        _logger_by_loop[loop_id] = logger_instance

        # Track loop lifecycle for cleanup (async context only)
        if loop is not None:
            _loop_refs[loop_id] = weakref.ref(loop)

    # Store in context variable for fast subsequent access
    _current_logger.set(logger_instance)

    return logger_instance


def get_logger(name: str | None = None):
    """Get a configured logger with true lazy initialization.

    Thread Safety: Uses per-event-loop storage.
    Each event loop gets its own SmartLogger instance.

    Args:
        name: Logger name (defaults to service name)

    Returns:
        logging.Logger instance
    """
    return _get_or_create_logger().get_logger(name)


def get_convenience_logger():
    """Get convenience logger with lazy initialization."""
    return get_logger("universal")


def get_health_status():
    """Get comprehensive health status and metrics from the logging system."""
    return _get_or_create_logger().get_health_status()


def _inject_string_truncation_filter(config: dict[str, Any], max_length: int) -> None:
    """
    DEPRECATED: LongStringTruncationFilter removed.

    Use format_json_for_log() from universal_logging.json_utils for JSON-specific
    truncation with Unicode support instead.

    This function now does nothing but is kept to prevent errors if max_string_length
    is still present in old configs.

    Args:
        config: Logging configuration dict (ignored)
        max_length: Maximum string length (ignored)
    """
    # No-op: filter removed, function kept for backward compatibility with old configs
    pass


def setup(config: dict[str, Any] | None = None) -> None:
    """
    Apply logging configuration with automatic truncation support.

    This is the recommended way to apply logging configuration when using
    universal_logging. It automatically handles:
    - truncate_logs: true - Truncates log FILES on startup
    - max_string_length: N - Truncates long STRINGS in log messages

    CRITICAL: When called with an explicit config, this marks the SmartLogger
    as initialized to PREVENT subsequent auto-discovery of logging.yaml files.
    This prevents worker processes from discovering the gateway's logging.yaml
    (with truncate_logs: true) and truncating the gateway's log file.

    Args:
        config: Logging configuration dict (standard logging.config.dictConfig format).
                May include:
                - 'truncate_logs: true' for automatic log file truncation
                - 'max_string_length: N' for automatic string truncation
                  (use -1 to disable truncation)
                If None, triggers SmartLogger auto-initialization from environment.

    Example:
        >>> from universal_logging import setup
        >>>
        >>> config = {
        ...     'version': 1,
        ...     'truncate_logs': True,        # Truncate log FILES
        ...     'max_string_length': 500,     # Truncate long STRINGS
        ...     'handlers': {...},
        ...     'loggers': {...}
        ... }
        >>>
        >>> setup(config)  # Both features applied automatically
    """
    if config is None:
        # Trigger SmartLogger auto-initialization
        _get_or_create_logger()
        return

    from .bootstrap import bootstrap_logger

    bootstrap_logger.debug("Setup called with explicit config")

    # Make a copy to avoid mutating the original config
    config_copy = config.copy()

    # Extract automatic feature flags
    truncate_logs = config_copy.pop("truncate_logs", False)
    max_string_length = config_copy.pop("max_string_length", None)
    head_chars = config_copy.pop("head_chars", None)
    tail_chars = config_copy.pop("tail_chars", None)

    # Apply file truncation if requested
    if truncate_logs:
        from .handler_cleanup import close_all_file_handlers

        bootstrap_logger.debug("Applying file truncation")
        handlers_closed = close_all_file_handlers()
        bootstrap_logger.debug(f"Closed {handlers_closed} handlers for truncation")

    # Apply string length truncation if configured
    if max_string_length is not None:
        # Re-add head/tail to config for filter injection
        if head_chars is not None:
            config_copy["head_chars"] = head_chars
        if tail_chars is not None:
            config_copy["tail_chars"] = tail_chars
        _inject_string_truncation_filter(config_copy, max_string_length)

    # Apply configuration with automatic handler cleanup and auto-flush
    # This handles truncate_logs, replaces FileHandler with AutoFlushFileHandler,
    # and applies the config via dictConfig
    from .smart_logger.handlers import apply_logging_config_with_cleanup

    # Re-add truncate_logs to config_copy so apply_logging_config_with_cleanup
    # can handle it (it was popped earlier)
    if truncate_logs:
        config_copy["truncate_logs"] = True

    apply_logging_config_with_cleanup(config_copy)

    # CRITICAL: Mark SmartLogger as initialized to prevent subsequent auto-discovery.
    # This prevents worker processes from discovering the gateway's logging.yaml
    # (with truncate_logs: true) and truncating the gateway's log file when
    # get_logger() is called later during module imports.
    #
    # We must properly initialize SmartLogger state (service_name, workspace_root, etc.)
    # without re-loading/re-applying config (which would trigger truncation again).
    try:
        smart_logger = _get_or_create_logger()
        if not smart_logger._initialized:
            # Set context detection attributes that would normally be set
            # during auto-init
            smart_logger._setup_monitoring()
            smart_logger._detect_context()

            # Normalize and store the config (same transformations as
            # _load_configuration)
            from .smart_logger.configuration import (
                normalize_log_levels,
                sanitize_formatters,
            )

            normalized_config = sanitize_formatters(config_copy.copy())
            normalized_config = normalize_log_levels(normalized_config)
            smart_logger.config = normalized_config

            # Mark as initialized to skip auto-discovery
            smart_logger._initialized = True
            bootstrap_logger.debug(
                f"SmartLogger initialized with explicit config, "
                f"service={smart_logger.service_name}"
            )
    except Exception as e:
        # Don't let SmartLogger setup failures break logging
        bootstrap_logger.warning(f"SmartLogger setup after config apply failed: {e}")

    bootstrap_logger.debug("Setup complete")


# Lazy imports for __all__ exports
def __getattr__(name: str):
    """Lazy import for module attributes."""
    if name == "SmartLogger":
        from .smart_logger import SmartLogger

        return SmartLogger
    elif name in ("EnhancedFormatter", "ColoredFormatter"):
        raise AttributeError(
            f"{name} has been removed. "
            f"Use universal_logging.renderers.JSONFormatter instead."
        )
    elif name == "close_all_file_handlers":
        from .handler_cleanup import close_all_file_handlers

        return close_all_file_handlers
    elif name == "LongStringTruncationFilter":
        # DEPRECATED: Use format_json_for_log() from json_utils instead
        raise AttributeError(
            "LongStringTruncationFilter has been removed. "
            "Use format_json_for_log() from universal_logging.json_utils for "
            "JSON-specific truncation with Unicode support."
        )
    elif name == "AutoFlushFileHandler":
        from .handlers import AutoFlushFileHandler

        return AutoFlushFileHandler
    elif name == "BootstrapLogger":
        from .bootstrap import BootstrapLogger

        return BootstrapLogger
    elif name == "BootstrapLevel":
        from .bootstrap import BootstrapLevel

        return BootstrapLevel
    elif name == "bootstrap_logger":
        from .bootstrap import bootstrap_logger

        return bootstrap_logger
    elif name == "format_json_for_log":
        from .json_utils import format_json_for_log

        return format_json_for_log
    elif name == "format_json_compact":
        from .json_utils import format_json_compact

        return format_json_compact
    elif name == "truncate_json_fields":
        from .json_utils import truncate_json_fields

        return truncate_json_fields
    elif name == "JSONFormatter":
        from .renderers import JSONFormatter

        return JSONFormatter
    elif name == "CompactJSONRenderer":
        from .renderers import CompactJSONRenderer

        return CompactJSONRenderer
    elif name == "PrettyJSONRenderer":
        from .renderers import PrettyJSONRenderer

        return PrettyJSONRenderer
    elif name == "ColorizedJSONRenderer":
        from .renderers import ColorizedJSONRenderer

        return ColorizedJSONRenderer
    elif name == "build_canonical_record":
        from .schema import build_canonical_record

        return build_canonical_record
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


__all__ = [
    # Core API
    "get_logger",
    "get_convenience_logger",
    "get_health_status",
    "setup",
    "SmartLogger",
    "close_all_file_handlers",
    "AutoFlushFileHandler",
    # JSON formatting utilities (replaces LongStringTruncationFilter)
    "format_json_for_log",
    "format_json_compact",
    "truncate_json_fields",
    # Structured logging (new)
    "JSONFormatter",
    "CompactJSONRenderer",
    "PrettyJSONRenderer",
    "ColorizedJSONRenderer",
    "build_canonical_record",
    # Bootstrap logger (for debugging universal_logging itself)
    "BootstrapLogger",
    "BootstrapLevel",
    "bootstrap_logger",
    # Logging level constants (standard Python logging levels)
    "CRITICAL",
    "DEBUG",
    "ERROR",
    "FATAL",
    "INFO",
    "NOTSET",
    "WARNING",
]

__version__ = "2.1.0"  # Bumped for async-safe redesign

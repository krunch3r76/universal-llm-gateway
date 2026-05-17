"""Lifecycle logging bootstrap and lifecycle logger access.

Owns LOG_DIR fallback, YAML logging config application, late logger initialization,
and early-startup exception reporting without stdlib `logging` (per project
invariant: always use `universal_logging.get_logger`).
"""

import os
from pathlib import Path

from universal_logging import get_logger

from ...core.config_loader import ConfigLoader

_lifecycle_logger = None
_gateway_logger = None


def setup_logging_from_config(config_loader: ConfigLoader) -> None:
    """Setup logging by loading and applying logging.yaml configuration.

    Note: LOG_DIR environment variable should already be set by main.py
    before any imports to prevent early logger initialization issues.
    """
    try:
        log_dir = os.getenv("LOG_DIR")
        if not log_dir:
            # Fallback if somehow not set (should never happen in production)
            data_dir = os.getenv("DATA_DIR", "/tmp")
            log_dir = os.path.join(data_dir, "logs", "universal-llm-gateway")
            Path(log_dir).mkdir(parents=True, exist_ok=True)
            os.environ["LOG_DIR"] = log_dir
            print(f"[LOGGING] Warning: LOG_DIR not set, using fallback: {log_dir}")

        # Load logging configuration from YAML
        logging_config = config_loader.load_logging_config()

        if logging_config:
            # Expand environment variables (${VAR:-default} syntax) in ALL values.
            # yaml.safe_load doesn't expand env vars, so ${LOG_LEVEL:-INFO} would be
            # passed as a literal string, causing "Unable to configure handler" errors
            from universal_logging.config_discovery import expand_env_vars

            logging_config = expand_env_vars(logging_config)

            # Apply configuration with automatic truncation support
            from universal_logging import setup

            setup(logging_config)

            print(f"[LOGGING] Configuration from YAML applied to {log_dir}")
        else:
            print(
                f"[LOGGING] No YAML config found, "
                f"universal_logging auto-initialized to {log_dir}"
            )

    except Exception as e:
        # Fallback setup - still let universal_logging handle it
        print(
            f"[LOGGING] Warning: setup failed ({e}), "
            "universal_logging will use emergency fallback"
        )


def initialize_lifecycle_loggers() -> None:
    """Initialize the module-private lifecycle and gateway loggers.

    Must be called immediately after setup_logging_from_config so that
    universal_logging has the correct LOG_DIR and YAML-applied configuration.
    Sets the private _lifecycle_logger and _gateway_logger used by getters.
    """
    global _lifecycle_logger, _gateway_logger
    _lifecycle_logger = get_logger(__name__)
    _gateway_logger = get_logger("universal_llm_gateway.main")


def get_lifecycle_logger():
    """Return the lifecycle logger (for this module's __name__), or None if not initialized."""
    return _lifecycle_logger


def get_gateway_logger():
    """Return the primary gateway logger ('universal_llm_gateway.main'), or None if not initialized."""
    return _gateway_logger


def log_startup_exception(error: Exception) -> None:
    """Log a startup exception with full traceback.

    Uses the gateway logger if it has already been initialized via
    initialize_lifecycle_loggers(). Otherwise falls back to a fresh
    get_logger("universal_llm_gateway.main") call (which triggers
    universal_logging's own fallback mechanisms). Never imports or uses
    the stdlib `logging` module.
    """
    msg = f"Failed to start Universal LLM Gateway (early error): {error}"
    if _gateway_logger is not None:
        _gateway_logger.error(msg, exc_info=True)
    else:
        fallback_logger = get_logger("universal_llm_gateway.main")
        fallback_logger.error(msg, exc_info=True)

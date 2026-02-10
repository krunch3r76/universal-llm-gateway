"""
Handler Management

Re-exports for backward compatibility and clean imports.
"""

from .config_apply import apply_logging_config_with_cleanup
from .setup import setup_handlers
from .verification import verify_handlers

# Internal imports for backward compatibility
# These were previously in the monolithic handlers.py
__all__ = [
    "setup_handlers",
    "verify_handlers",
    "apply_logging_config_with_cleanup",
]

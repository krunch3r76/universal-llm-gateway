"""
Handler cleanup utilities for log truncation support.

Provides helpers to close and remove existing FileHandler instances
before applying logging configuration via dictConfig().

Background:
-----------
When disable_existing_loggers: false is set (default in our configs),
logging.config.dictConfig() REUSES existing FileHandler instances
instead of recreating them. This means the 'mode' setting in the
config dict is IGNORED for existing handlers.

Solution:
---------
Explicitly close and remove all FileHandler instances before calling
dictConfig(). This forces dictConfig() to create NEW handlers with
the specified mode (e.g., mode='w' for truncation).

Usage:
------
```python
from universal_logging.handler_cleanup import close_all_file_handlers
import logging.config

# Before applying config with truncate_logs: true
close_all_file_handlers()

# Now dictConfig creates NEW handlers with mode='w'
logging.config.dictConfig(config)
```

Invariant:
----------
∀ logger ∈ {root} ∪ configured_loggers:
  post(close_all_file_handlers) ⟹
  |{h ∈ logger.handlers, isinstance(h, FileHandler)}| = 0
"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from logging import Logger


def close_all_file_handlers() -> int:
    """
    Close and remove all FileHandler instances from all loggers.

    This function walks through the root logger and all configured loggers,
    closes any FileHandler instances (releasing file handles), and removes
    them from the logger's handler list.

    This is necessary before calling logging.config.dictConfig() when
    truncate_logs: true is set, to force creation of NEW handlers with
    mode='w' instead of reusing existing handlers with mode='a'.

    Returns:
        int: Number of FileHandler instances closed and removed

    Example:
        >>> import logging.config
        >>> from universal_logging.handler_cleanup import close_all_file_handlers
        >>>
        >>> # Close existing handlers
        >>> count = close_all_file_handlers()
        >>> print(f"Closed {count} file handlers")
        >>>
        >>> # Now dictConfig creates fresh handlers with mode='w'
        >>> logging.config.dictConfig(config_with_truncate_logs)

    Note:
        This function is idempotent - calling it multiple times is safe.
    """
    handlers_closed = 0

    # Close handlers from root logger
    root_logger: Logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        if isinstance(handler, logging.FileHandler):
            handler.close()
            root_logger.removeHandler(handler)
            handlers_closed += 1

    # Close handlers from all configured loggers
    # NOTE: Use logging.getLogger() directly to avoid triggering SmartLogger
    # initialization which would cause infinite recursion during cleanup
    for logger_name in list(logging.Logger.manager.loggerDict.keys()):
        logger: Logger = logging.getLogger(logger_name)
        if hasattr(logger, "handlers"):
            for handler in logger.handlers[:]:
                if isinstance(handler, logging.FileHandler):
                    handler.close()
                    logger.removeHandler(handler)
                    handlers_closed += 1

    return handlers_closed

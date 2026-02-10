"""
Custom logging handlers for universal_logging.

Provides auto-flushing FileHandler that ensures log messages are written
immediately to disk rather than being buffered.
"""

from .auto_flush_file_handler import AutoFlushFileHandler

__all__ = ["AutoFlushFileHandler"]

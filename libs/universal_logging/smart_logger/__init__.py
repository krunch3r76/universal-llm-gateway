"""
SmartLogger Module

Auto-initializing logger that detects context and configures itself
without any manual intervention. Production-ready with comprehensive
error handling, monitoring, and fault tolerance.
"""

from .core import SmartLogger

__all__ = ["SmartLogger"]

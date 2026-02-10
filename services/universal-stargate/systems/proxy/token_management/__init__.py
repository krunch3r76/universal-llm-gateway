"""
Token management package for Universal Stargate Proxy.

This package provides token counting and allocation functionality
for managing generation token limits in LLM requests.
"""

from .token_counting import TokenCountResult
from .token_manager import TokenManager

__all__ = ["TokenManager", "TokenCountResult"]

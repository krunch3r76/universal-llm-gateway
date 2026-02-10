"""
Utility modules for universal_transport.

Provides reusable patterns for common transport operations.
"""

from .expiring_item import ExpiringItem, ExpiringRegistry

__all__ = ["ExpiringItem", "ExpiringRegistry"]

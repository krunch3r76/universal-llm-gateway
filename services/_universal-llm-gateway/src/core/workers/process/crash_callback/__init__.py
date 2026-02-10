"""
Process crash callback handling - event-driven cleanup.

Orchestrates crash detection response with isolated error handling.
"""

from .orchestrator import handle_process_crash_callback

__all__ = ["handle_process_crash_callback"]

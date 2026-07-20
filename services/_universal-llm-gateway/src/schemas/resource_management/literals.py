"""Literal type aliases for queue management API model status and priority levels.

These shared aliases constrain Pydantic fields across resource status, model
status, and load/unload request schemas in the resource management package.
"""

from typing import Literal

ModelStatus = Literal["not_loaded", "loading", "loaded", "busy", "unloading", "error"]
PriorityLevel = Literal["high", "normal", "low"]

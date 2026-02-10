"""
Type definitions for hot reload functionality.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class ErrorType(StrEnum):
    """Error types for hot reload operations"""

    STARTUP = "startup_error"
    STOP = "stop_error"
    VALIDATION = "validation_error"
    MEMORY_UPDATE = "memory_update_error"
    PERSISTENCE = "persistence_error"


@dataclass
class ReloadEvent:
    """Represents a configuration reload event"""

    file_path: str
    model_key: str | None
    success: bool
    timestamp: datetime
    error: str | None = None
    duration_ms: float | None = None


@dataclass
class HotReloadStatus:
    """Status information for hot reload functionality"""

    enabled: bool
    watch_directory: str
    last_reload: datetime | None
    recent_changes: list[ReloadEvent] = field(default_factory=list)
    error_count: int = 0

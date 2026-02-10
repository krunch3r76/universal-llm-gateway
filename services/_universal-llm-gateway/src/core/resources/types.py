"""Resource tracking data types.

Defines core data structures for model resource tracking:
- ModelStatus: Enum for model lifecycle states
- ModelResourceInfo: Per-model resource and state tracking
- SystemResourceInfo: System-wide resource snapshot
"""

import time
from dataclasses import dataclass, field
from enum import Enum


class ModelStatus(Enum):
    """Model status enumeration for lifecycle tracking."""

    NOT_LOADED = "not_loaded"
    LOADING = "loading"
    LOADED = "loaded"
    BUSY = "busy"
    UNLOADING = "unloading"
    ERROR = "error"


@dataclass
class ModelResourceInfo:
    """Resource information for a specific model.

    Tracks both resource usage (VRAM/RAM) and operational state
    (inference timing, process info, errors).
    """

    model_id: str
    status: ModelStatus = ModelStatus.NOT_LOADED
    inference_state: str | None = None  # 'token_counting' or 'generating' when busy
    vram_usage_mb: int = 0
    ram_usage_mb: int = 0
    current_inference_start: float | None = None
    last_inference_end: float | None = None
    load_time: float | None = None
    last_inference_time: float | None = None  # Track last inference for LRU eviction
    error_message: str | None = None
    process_pid: int | None = None
    last_updated: float = field(default_factory=time.time)


@dataclass
class SystemResourceInfo:
    """System-wide resource information snapshot.

    Captures total and available VRAM/RAM at a point in time.
    """

    total_vram_mb: int = 0
    available_vram_mb: int = 0
    total_ram_mb: int = 0
    available_ram_mb: int = 0
    timestamp: float = field(default_factory=time.time)

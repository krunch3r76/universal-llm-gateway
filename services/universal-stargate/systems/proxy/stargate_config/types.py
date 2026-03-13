"""Typed dictionary contracts for Stargate debug and pipeline event settings.

These types make the expected persistence payload shape explicit across the
proxy lifecycle and monitoring code paths that consume configuration.
"""

from typing import TypedDict


class DebugEventPersistenceConfig(TypedDict):
    """Persistence settings for debug event files used by rotation and flush scheduling.

    This contract keeps file writer policy explicit for lifecycle components
    that persist operational events to local disk.
    """

    enabled: bool
    directory: str
    max_file_size_mb: int
    max_files: int
    flush_interval_seconds: float


class DebugEventConfig(TypedDict):
    """Top-level debug event settings with persistence policy and optional
    socket output.

    The proxy consumes this shape to enable file persistence while optionally
    exposing a live event stream endpoint for local debugging.
    """

    persistence: DebugEventPersistenceConfig
    socket_path: str | None


class PipelineEventConfig(TypedDict):
    """Dedicated pipeline event persistence policy with routing filter configuration.

    This type isolates pipeline event retention settings from general debug
    events so operators can tune write volume independently.
    """

    enabled: bool
    directory: str
    max_file_size_mb: int
    max_files: int
    flush_interval_seconds: float
    signal_filter: str

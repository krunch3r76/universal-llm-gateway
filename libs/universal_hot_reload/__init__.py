"""
Universal hot-reload library using watchfiles (async, Rust-based).

Provides pure async file monitoring with debouncing.
Used by Gateway, Stargate, and any service needing file watching.
"""

from .path_filters import matches_watch_exclude
from .timestamp_preserving_io import read_text_preserving_timestamps
from .watcher import HotReloadWatcher

__all__ = [
    "HotReloadWatcher",
    "matches_watch_exclude",
    "read_text_preserving_timestamps",
]

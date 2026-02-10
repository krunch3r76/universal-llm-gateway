"""
Universal hot-reload library using watchfiles (async, Rust-based).

Provides pure async file monitoring with debouncing.
Used by Gateway, Stargate, and any service needing file watching.
"""
from .timestamp_preserving_io import read_text_preserving_timestamps
from .watcher import HotReloadWatcher

__all__ = ["HotReloadWatcher", "read_text_preserving_timestamps"]

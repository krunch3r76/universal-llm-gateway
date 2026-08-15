"""Bounded process-local cache for model-rate YAML parsing.

The dispatch economics rollup resolves rates once per output row. This module
keeps YAML parsing off that hot path while invalidating entries when source
files change, so runtime catalog refreshes remain visible without polling.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_YAML_CACHE: dict[tuple[str, int, int], dict[str, Any]] = {}


def _file_key(path: Path) -> tuple[str, int, int] | None:
    """Return a content-change key, or ``None`` when the source is absent."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return (str(path.resolve()), stat.st_mtime_ns, stat.st_size)


def load_yaml_payload(path: Path) -> dict[str, Any]:
    """Read and parse a YAML mapping once per file version.

    Missing, malformed, or non-mapping documents produce an empty mapping.
    Cached values are immutable by convention; callers must not mutate them.
    """
    key = _file_key(path)
    if key is None:
        return {}
    cached = _YAML_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        payload = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.CSafeLoader)
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(payload, dict):
        return {}
    _YAML_CACHE[key] = payload
    if len(_YAML_CACHE) > 32:
        oldest = next(iter(_YAML_CACHE))
        _YAML_CACHE.pop(oldest, None)
    return payload


def clear_rate_caches() -> None:
    """Drop parsed YAML entries after tests or explicit runtime refreshes."""
    _YAML_CACHE.clear()

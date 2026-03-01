from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path.home() / ".rag" / "config.yaml"


@dataclass(slots=True, kw_only=True)
class WatchDirectory:
    path: str
    extensions: list[str]
    recursive: bool = True
    chunk_tokens: int | None = None


@dataclass(slots=True, kw_only=True)
class RagConfig:
    watch_directories: list[WatchDirectory]


def _normalize_extensions(raw_extensions: object) -> list[str]:
    if not isinstance(raw_extensions, list):
        return []
    normalized: list[str] = []
    for item in raw_extensions:
        if isinstance(item, str) and item.strip():
            normalized.append(f".{item.strip().lstrip('.')}")
    return normalized


def load_config() -> RagConfig:
    """Load ~/.rag/config.yaml and return parsed watcher configuration."""
    if not _CONFIG_PATH.exists():
        return RagConfig(watch_directories=[])

    try:
        loaded: object = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        logger.error("Failed to parse RAG config: path=%s", _CONFIG_PATH, exc_info=True)
        return RagConfig(watch_directories=[])

    if not isinstance(loaded, dict):
        logger.error("Invalid RAG config root type: expected mapping")
        return RagConfig(watch_directories=[])
    parsed_root: dict[str, object] = {
        key: value for key, value in loaded.items() if isinstance(key, str)
    }

    raw_watchers = parsed_root.get("watch_directories", [])
    if not isinstance(raw_watchers, list):
        logger.error("Invalid watch_directories type: expected list")
        return RagConfig(watch_directories=[])

    watch_directories: list[WatchDirectory] = []
    for raw_item in raw_watchers:
        if not isinstance(raw_item, dict):
            logger.warning("Skipping watch entry with invalid type: %r", raw_item)
            continue
        item: dict[str, object] = {
            key: value for key, value in raw_item.items() if isinstance(key, str)
        }
        path = item.get("path")
        if not isinstance(path, str) or not path.strip():
            logger.warning("Skipping watch entry without valid path: %r", item)
            continue
        recursive_value = item.get("recursive", True)
        recursive = recursive_value if isinstance(recursive_value, bool) else True
        raw_chunk_tokens = item.get("chunk_tokens")
        chunk_tokens = raw_chunk_tokens if isinstance(raw_chunk_tokens, int) else None
        watch_directories.append(
            WatchDirectory(
                path=path.strip(),
                extensions=_normalize_extensions(item.get("extensions")),
                recursive=recursive,
                chunk_tokens=chunk_tokens,
            )
        )

    return RagConfig(watch_directories=watch_directories)

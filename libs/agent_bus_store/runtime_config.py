"""Apply host ``~/.gateway/agent-bus.yaml`` flags into ``os.environ`` at start."""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger("agent-bus")

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_CONFIG_PATH = Path.home() / ".gateway" / "agent-bus.yaml"


def _parse_bool(value: object, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _TRUTHY:
            return True
        if normalized in ("0", "false", "no", "off", ""):
            return False
    return default


def apply_runtime_config_env() -> None:
    """Load feature flags from ``~/.gateway/agent-bus.yaml`` when unset in environ."""
    if not _CONFIG_PATH.exists():
        return
    try:
        import yaml

        raw = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        logger.warning("failed to read %s", _CONFIG_PATH, exc_info=True)
        return
    if not isinstance(raw, dict):
        return
    if _parse_bool(raw.get("checkpoint_auto_supersede")):
        os.environ.setdefault("AGENT_BUS_CHECKPOINT_AUTO_SUPERSEDE", "1")


__all__ = ["apply_runtime_config_env"]

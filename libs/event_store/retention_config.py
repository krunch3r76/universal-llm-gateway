"""Live retention settings from ~/.gateway/event-service.yaml.

Fails closed: only an explicit string ``run`` enables deletes. A missing file,
parse error, non-mapping body, missing or mistyped ``automatic``, or YAML
``false``/``off``/``no`` resolves to ``dry_run`` or ``off`` — never ``run``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG = Path.home() / ".gateway" / "event-service.yaml"
_VALID_AUTOMATIC = frozenset({"dry_run", "run", "off"})
_OFF_WORDS = frozenset({"off", "false", "no", "0"})


@dataclass(frozen=True, slots=True)
class RetentionSettings:
    """Retention policy read from disk on each automatic pass."""

    retention_days: int
    max_sessions: int
    automatic: str


def _config_path() -> Path:
    raw = os.environ.get("EVENT_SERVICE_CONFIG_PATH", str(_DEFAULT_CONFIG))
    return Path(raw).expanduser()


def _parse_automatic(raw: Any) -> str:
    """Map a yaml ``automatic`` value to a mode; anything unrecognized is dry_run."""
    if raw is False:
        return "off"
    if isinstance(raw, str):
        mode = raw.strip().lower()
        if mode in _VALID_AUTOMATIC:
            return mode
        if mode in _OFF_WORDS:
            return "off"
    if raw is not None:
        logger.error("retention.automatic=%r is not dry_run|run|off; using dry_run", raw)
    return "dry_run"


def _positive_int(raw: Any, fallback: int, key: str) -> int:
    if raw is None:
        return fallback
    if isinstance(raw, bool) or not isinstance(raw, int | str):
        logger.error("retention.%s=%r is not an integer; using %d", key, raw, fallback)
        return fallback
    try:
        value = int(raw)
    except ValueError:
        logger.error("retention.%s=%r is not an integer; using %d", key, raw, fallback)
        return fallback
    return max(1, value)


def load_retention_settings(
    *,
    cli_retention_days: int = 7,
    cli_max_sessions: int = 2,
) -> RetentionSettings:
    """Load retention.days / max_sessions / automatic from the live gateway file.

    Missing or invalid ``days`` / ``max_sessions`` fall back to the CLI values
    the service was spawned with. ``EVENTS_RETENTION_STARTUP`` overrides
    ``automatic`` only when it is a valid mode.
    """
    fallback = RetentionSettings(
        retention_days=max(1, int(cli_retention_days)),
        max_sessions=max(1, int(cli_max_sessions)),
        automatic=_env_automatic_override() or "dry_run",
    )
    path = _config_path()
    if not path.is_file():
        return fallback
    try:
        import yaml

        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        logger.exception("Failed to parse event-service config: %s", path)
        return fallback
    if not isinstance(raw, dict):
        logger.error("event-service config %s is not a mapping; using dry_run", path)
        return fallback
    retention = raw.get("retention", {})
    if not isinstance(retention, dict):
        logger.error("retention section in %s is not a mapping; using dry_run", path)
        return fallback
    return RetentionSettings(
        retention_days=_positive_int(
            retention.get("days"), fallback.retention_days, "days"
        ),
        max_sessions=_positive_int(
            retention.get("max_sessions"), fallback.max_sessions, "max_sessions"
        ),
        automatic=_env_automatic_override()
        or _parse_automatic(retention.get("automatic")),
    )


def _env_automatic_override() -> str | None:
    raw = os.environ.get("EVENTS_RETENTION_STARTUP")
    if raw is None or not str(raw).strip():
        return None
    mode = str(raw).strip().lower()
    if mode in _VALID_AUTOMATIC:
        return mode
    logger.error(
        "EVENTS_RETENTION_STARTUP=%s is not dry_run|run|off; ignoring env override",
        raw,
    )
    return None

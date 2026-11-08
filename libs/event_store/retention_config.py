"""Live retention settings from ~/.gateway/event-service.yaml (not gateway event-service.yaml)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG = Path.home() / ".gateway" / "event-service.yaml"
_VALID_AUTOMATIC = frozenset({"dry_run", "run", "off"})


@dataclass(frozen=True, slots=True)
class RetentionSettings:
    """Retention policy read from disk on each automatic pass."""

    retention_days: int
    max_sessions: int
    automatic: str


def _config_path() -> Path:
    raw = os.environ.get("EVENT_SERVICE_CONFIG_PATH", str(_DEFAULT_CONFIG))
    return Path(raw).expanduser()


def _parse_boolish_automatic(raw: Any, *, file_present: bool) -> str:
    if isinstance(raw, str):
        mode = raw.strip().lower()
        if mode in _VALID_AUTOMATIC:
            return mode
    if raw is None:
        return "run" if file_present else "dry_run"
    logger.error(
        "retention.automatic=%r is not dry_run|run|off; using %s",
        raw,
        "run" if file_present else "dry_run",
    )
    return "run" if file_present else "dry_run"


def load_retention_settings(
    *,
    cli_retention_days: int | None = None,
    cli_max_sessions: int | None = None,
) -> RetentionSettings:
    """Load retention.days / max_sessions / automatic from the live gateway file.

    CLI values from the spawning manage invocation apply only when the config
    file is absent. ``EVENTS_RETENTION_STARTUP`` overrides ``automatic`` when set.
    """
    path = _config_path()
    fallback_days = int(cli_retention_days if cli_retention_days is not None else 7)
    fallback_sessions = int(cli_max_sessions if cli_max_sessions is not None else 2)
    if not path.is_file():
        return RetentionSettings(
            retention_days=fallback_days,
            max_sessions=fallback_sessions,
            automatic=_env_automatic_override() or "dry_run",
        )
    try:
        import yaml

        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        logger.exception("Failed to parse event-service config: %s", path)
        return RetentionSettings(
            retention_days=fallback_days,
            max_sessions=fallback_sessions,
            automatic=_env_automatic_override() or "run",
        )
    if not isinstance(raw, dict):
        return RetentionSettings(
            retention_days=fallback_days,
            max_sessions=fallback_sessions,
            automatic=_env_automatic_override() or "run",
        )
    retention = raw.get("retention", {})
    if not isinstance(retention, dict):
        retention = {}
    try:
        days = int(retention.get("days", 2))
    except (TypeError, ValueError):
        days = 2
    try:
        max_sessions = int(retention.get("max_sessions", 2))
    except (TypeError, ValueError):
        max_sessions = 2
    automatic = _env_automatic_override() or _parse_boolish_automatic(
        retention.get("automatic"), file_present=True
    )
    return RetentionSettings(
        retention_days=max(1, days),
        max_sessions=max(1, max_sessions),
        automatic=automatic,
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

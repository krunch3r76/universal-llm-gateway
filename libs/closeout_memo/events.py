"""Domain events for closeout-memo. Observation only; never fails the caller."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

SIGNAL_PREFIX = "stargate.closeout_memo"


def emit_closeout_memo(name: str, **payload: Any) -> None:
    """Best-effort Event Service emit. ``name`` is the suffix after the prefix."""
    signal = f"{SIGNAL_PREFIX}.{name}"
    body = {key: value for key, value in payload.items() if value is not None}
    try:
        from scripts.model_manager.observation_event import _emit_sync

        _emit_sync(signal, body, source="closeout_memo")
    except Exception:  # noqa: BLE001 — observation must not fail the writer
        logger.debug("closeout memo event skipped signal=%s", signal, exc_info=True)

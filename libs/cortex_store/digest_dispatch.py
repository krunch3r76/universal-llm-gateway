"""Fire-and-forget digest enqueue at session-close post-close seam.

Honors ``CORTEX_DIGEST_CLOSE_HOOK=1``. Fail-open: enqueue errors never
perturb ``session_close``.
"""

from __future__ import annotations

import os
import threading
from typing import Any

from universal_logging import get_logger

from .events_digest import digest_run

logger = get_logger("cortex-api.digest_dispatch")

_REQUIRED_KEYS = frozenset({"journal_entity_id", "entry_anchor", "entry_text"})


def _hook_enabled() -> bool:
    return os.environ.get("CORTEX_DIGEST_CLOSE_HOOK", "").strip() == "1"


def _validate_digest_args(digest_args: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(digest_args, dict):
        return None
    if not _REQUIRED_KEYS.issubset(digest_args.keys()):
        return None
    journal_entity_id = digest_args.get("journal_entity_id")
    entry_anchor = digest_args.get("entry_anchor")
    entry_text = digest_args.get("entry_text")
    if not journal_entity_id or not entry_anchor or not entry_text:
        return None
    payload: dict[str, Any] = {
        "journal_entity_id": str(journal_entity_id),
        "entry_anchor": str(entry_anchor),
        "entry_text": str(entry_text),
    }
    journal_uri = digest_args.get("journal_uri")
    if journal_uri:
        payload["journal_uri"] = str(journal_uri)
    return payload


def _run_digest(validated: dict[str, Any], session_id: str | None) -> None:
    from .dispatch_ops.ops_digest import _op_digest

    digest_run(
        journal_entity_id=validated["journal_entity_id"],
        entry_anchor=validated["entry_anchor"],
        session_id=session_id,
    )
    try:
        _op_digest(**validated)
    except Exception:
        logger.warning(
            "digest background dispatch failed for session %s anchor %s",
            session_id,
            validated.get("entry_anchor"),
            exc_info=True,
        )


def dispatch_digest_background(
    digest_args: dict[str, Any], *, session_id: str | None = None
) -> None:
    """Daemon-thread enqueue of _op_digest. Fail-open. Honor CORTEX_DIGEST_CLOSE_HOOK."""
    if not _hook_enabled():
        return
    validated = _validate_digest_args(digest_args)
    if validated is None:
        return
    try:
        threading.Thread(
            target=_run_digest,
            args=(validated, session_id),
            daemon=True,
        ).start()
    except Exception:
        logger.warning(
            "digest enqueue failed for session %s",
            session_id,
            exc_info=True,
        )

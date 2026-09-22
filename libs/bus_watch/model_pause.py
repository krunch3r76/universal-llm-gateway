"""Liaison model pause — ``policy.paused_models`` is the only source.

A missing or empty list pauses nothing. ``now_row`` prose is not a pause:
the house has carried the words ``ready=false`` in that sentence while the
spawn predicate kept reading ``policy.ready``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bus_watch.fable_lock import WATCH_DIR


def _canon(value: object) -> str:
    return str(value or "").strip().lower()


def _paused_entries(policy: dict[str, Any] | None) -> list[str]:
    raw = (policy or {}).get("paused_models")
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        return [_canon(raw)]
    if isinstance(raw, (list, tuple)):
        return [_canon(item) for item in raw if _canon(item)]
    return []


def model_matches_pause(entry: str, model: str) -> bool:
    """True when ``model`` is ``entry`` or an effort suffix of it.

    ``cdp/opus-5`` matches ``cdp/opus-5-high``. It does not match a longer
    id that merely shares a prefix without a hyphen boundary (``cdp/opus-50``).
    """
    paused = _canon(entry)
    target = _canon(model)
    if not paused or not target:
        return False
    if target == paused:
        return True
    return target.startswith(paused + "-")


def model_paused(policy: dict[str, Any] | None, model: str) -> bool:
    """True when ``model`` is listed in ``policy.paused_models``."""
    return any(model_matches_pause(entry, model) for entry in _paused_entries(policy))


def read_liaison_policy(
    root_id: str, *, watch_dir: Path | None = None
) -> dict[str, Any] | None:
    """Tick policy for ``root_id``, or None when the file is absent or unreadable.

    Callers that have no policy file keep their historical default. A present
    file with no ``paused_models`` is an unpaused policy, not a missing file.
    """
    rid = str(root_id or "").strip()
    if not rid or "/" in rid or "\\" in rid or rid.startswith("."):
        return None
    path = (watch_dir or WATCH_DIR) / f"liaison-{rid}.tick.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    policy = data.get("policy") if isinstance(data, dict) else None
    return policy if isinstance(policy, dict) else None


__all__ = [
    "model_matches_pause",
    "model_paused",
    "read_liaison_policy",
]

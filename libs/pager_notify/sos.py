"""Charter-tick SOS — once-per-root claim + pager for silent-starve class."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from pager_notify.client import notify_pager
from pager_notify.so_what import SMS_BODY_MAX, SMS_SUBJECT_MAX, clip


def _state_dir() -> Path:
    return Path(
        os.environ.get(
            "PAGER_NOTIFY_STATE_DIR",
            str(Path.home() / ".local" / "share" / "pager-notify"),
        )
    )


def _sos_dedupe_file() -> Path:
    return _state_dir() / "tick_sos_dedupe.json"


def _sos_enabled() -> bool:
    raw = os.environ.get("PAGER_NOTIFY_TICK_SOS", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _load() -> dict[str, float]:
    path = _sos_dedupe_file()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return {str(k): float(v) for k, v in raw.items()}
    except (OSError, ValueError, TypeError):
        pass
    return {}


def _save(state: dict[str, float]) -> None:
    path = _sos_dedupe_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def claim_tick_sos(
    root_id: str,
    reason: str,
    *,
    now: float | None = None,
    ttl_s: float | None = None,
) -> bool:
    """Claim one SOS page/dispatch for ``root_id`` within *ttl_s*.

    Episode claim key is ``root_id`` alone (F2 — ≤1 concurrent mission).
    Default TTL 3600s — re-page if the stall survives an hour after a failed heal.
    """
    if ttl_s is None:
        try:
            ttl_s = float(os.environ.get("CHARTER_TICK_SOS_TTL_S", "3600"))
        except ValueError:
            ttl_s = 3600.0
    epoch = time.time() if now is None else now
    key = str(root_id)
    state = _load()
    prior = state.get(key)
    if prior is not None and (epoch - prior) < ttl_s:
        return False
    state[key] = epoch
    _save(state)
    return True


async def notify_tick_sos(
    *,
    root_id: str,
    reason: str,
    detail: str = "",
    consecutive: int = 0,
) -> bool:
    """Awareness SOS — ¬ COME TO IDE. Kaywan digs via cursor-auto / CDP operator."""
    if not _sos_enabled():
        return False
    subject = clip(
        f"ULG tick SOS · #{root_id} {reason}",
        SMS_SUBJECT_MAX,
    )
    parts = [
        f"root={root_id}",
        f"reason={reason}",
        f"n={consecutive}" if consecutive else "",
        clip(detail, 160) if detail else "",
        "details via cursor-auto / CDP operator",
    ]
    body = clip(" · ".join(p for p in parts if p), SMS_BODY_MAX)
    return await notify_pager(subject, body, tag="charter-tick-sos")


__all__ = [
    "claim_tick_sos",
    "notify_tick_sos",
]

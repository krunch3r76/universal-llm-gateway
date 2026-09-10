"""Lane-scoped TAB_READY liveness — falsifiable readiness for the keystroke bridge."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

_CURSOR = "cursor"
_LIFE = "web-anthropic"

TAB_READY_TTL_S = int(os.environ.get("CURSOR_BRIDGE_TAB_READY_TTL_S", "600"))


def _subject(turn: dict[str, Any]) -> str:
    return str(turn.get("subject") or "").strip().upper()


def _sender(turn: dict[str, Any]) -> str:
    return str(turn.get("from") or "")


def _turn_number(turn: dict[str, Any]) -> int:
    return int(turn.get("turn_number") or 0)


def _parse_created_at(raw: str) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).astimezone(UTC)
    except ValueError:
        return None


def lane_epoch_turn(turns: list[dict[str, Any]]) -> int:
    """Turn after which TAB_READY must appear — latest BRIDGE_OPEN or TAB_GONE."""
    epoch = 0
    for turn in sorted(turns, key=_turn_number):
        n = _turn_number(turn)
        subj = _subject(turn)
        sender = _sender(turn)
        if subj.startswith("BRIDGE_OPEN") and sender != _CURSOR:
            epoch = n
        elif subj.startswith("TAB_GONE") and sender == _CURSOR:
            epoch = n
    return epoch


def _latest_live_signal(
    turns: list[dict[str, Any]], *, after_turn: int
) -> tuple[int | None, datetime | None, str | None]:
    """Return (turn_number, created_at, subject) for newest TAB_READY/TAB_ALIVE after epoch."""
    best: tuple[int, datetime, str] | None = None
    for turn in turns:
        n = _turn_number(turn)
        if n <= after_turn:
            continue
        if _sender(turn) != _CURSOR:
            continue
        subj = _subject(turn)
        if not (subj.startswith("TAB_READY") or subj.startswith("TAB_ALIVE")):
            continue
        created = _parse_created_at(str(turn.get("created_at") or ""))
        if created is None:
            continue
        if best is None or n > best[0]:
            best = (n, created, subj)
    if best is None:
        return None, None, None
    return best


def assess_lane_readiness(
    turns: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    ttl_s: int | None = None,
) -> dict[str, Any]:
    """Decide whether a MSG wake may proceed on this lane.

    Bind (c): lane-scoped (TAB_READY must follow latest BRIDGE_OPEN/TAB_GONE) plus
    liveness (signal must be within TTL). Stale or pre-epoch signals → not ready.
    """
    ttl = TAB_READY_TTL_S if ttl_s is None else ttl_s
    now = now or datetime.now(UTC)
    epoch = lane_epoch_turn(turns)
    turn, created, subj = _latest_live_signal(turns, after_turn=epoch)
    base = {
        "bridge_open_turn": epoch or None,
        "tab_ready_ttl_s": ttl,
    }
    if turn is None:
        return {
            **base,
            "ready": False,
            "turn": None,
            "reason": "no_tab_ready",
            "tab_ready_age_s": None,
        }
    age_s = max(0.0, (now - created).total_seconds())
    if age_s > ttl:
        return {
            **base,
            "ready": False,
            "turn": turn,
            "reason": "tab_ready_stale",
            "tab_ready_age_s": round(age_s, 1),
            "tab_ready_subject": subj,
        }
    return {
        **base,
        "ready": True,
        "turn": turn,
        "reason": None,
        "tab_ready_age_s": round(age_s, 1),
        "tab_ready_subject": subj,
    }

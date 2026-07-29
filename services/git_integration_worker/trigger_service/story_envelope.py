"""Trigger-row story envelope election and emit-site payload stamping."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from systems.frontier_consult.story_wire import (
    DEFAULT_TRIGGER_PURPOSE,
    PURPOSE_UNSTATED,
)
from universal_logging import get_logger

from .models import TriggerRow

logger = get_logger(__name__)

# Closed vocabulary — rung 1 (caller-supplied id) deferred; Gate-2 amendment required.
STORY_ID_SOURCE_VOCABULARY = frozenset(
    {"charter_window", "mission_arc", "fallback_trigger", "unelected"}
)

_stamp_degrade_count = 0


@dataclass(frozen=True, slots=True)
class TriggerStoryEnvelope:
    story_id: str
    story_id_source: str
    asked_by: str
    purpose: str
    purpose_source: str


def stamp_degrade_count() -> int:
    """Witness counter for S-B6 degrade-not-drop stamp failures."""
    return _stamp_degrade_count


def _require_trigger_id(trigger_id: str | None) -> str:
    if not trigger_id or not str(trigger_id).strip():
        raise ValueError("trigger_id required for story envelope election")
    return str(trigger_id).strip()


def elect_trigger_story_envelope(
    *,
    trigger_id: str,
    created_by: str,
    purpose: str = DEFAULT_TRIGGER_PURPOSE,
    so_what: str | None = None,
    arc: str | None = None,
    charter_root: str | None = None,
    window_index: int | None = None,
    nest_under: str | None = None,
    **_ignored: Any,
) -> TriggerStoryEnvelope:
    """Elect story envelope at schedule time (rungs 2–5 only).

    Ladder rungs **2–5** in order (rung 1 caller-supplied deferred — Gate-2
    vocabulary amendment required to admit). Closed ``story_id_source`` vocabulary:
    ``{charter_window, mission_arc, fallback_trigger, unelected}``.

    FORBIDDEN inputs that must not influence election: ``nest_under``, lease
    handles, park-stack identifiers. Election runs once at schedule and is
    never re-run at fire.
    """
    tid = _require_trigger_id(trigger_id)
    _ = nest_under  # explicit ignore — topology must not steer election

    if charter_root and charter_root.strip() and window_index is not None:
        story_id = f"{charter_root.strip()}#{window_index}"
        story_id_source = "charter_window"
    elif arc and str(arc).strip():
        story_id = str(arc).strip()
        story_id_source = "mission_arc"
    else:
        story_id = tid
        story_id_source = "fallback_trigger"

    purpose_val, purpose_source = _resolve_purpose(purpose, so_what)
    return TriggerStoryEnvelope(
        story_id=story_id,
        story_id_source=story_id_source,
        asked_by=created_by.strip() or "unknown",
        purpose=purpose_val,
        purpose_source=purpose_source,
    )


def _resolve_purpose(
    purpose: str | None,
    so_what: str | None,
) -> tuple[str, str]:
    purpose_text = (purpose or "").strip()
    so_what_text = (so_what or "").strip()
    is_default_or_empty = not purpose_text or purpose_text == DEFAULT_TRIGGER_PURPOSE
    if is_default_or_empty and so_what_text:
        return so_what_text, "so_what"
    if purpose_text and not is_default_or_empty:
        return purpose_text, "purpose"
    return PURPOSE_UNSTATED, "unstated"


def _envelope_from_row(row: TriggerRow) -> TriggerStoryEnvelope:
    tid = _require_trigger_id(row.id)
    if row.story_id is not None and row.story_id_source is not None:
        story_id = row.story_id
        story_id_source = row.story_id_source
    else:
        story_id = tid
        story_id_source = "unelected"
    purpose_val, purpose_source = _resolve_purpose(row.purpose, row.so_what)
    return TriggerStoryEnvelope(
        story_id=story_id,
        story_id_source=story_id_source,
        asked_by=row.created_by.strip() or "unknown",
        purpose=purpose_val,
        purpose_source=purpose_source,
    )


def stamp_trigger_envelope(payload: dict[str, Any], row: TriggerRow) -> dict[str, Any]:
    """Write mandatory envelope keys onto an emit payload from a trigger row.

    Always writes ``story_id``, ``story_id_source``, ``asked_by``, ``purpose``,
    and ``purpose_source``. Non-envelope required keys in ``payload`` are never
    mutated. ``story_id`` is never derived from any other event field.

    Legacy rows with NULL persisted envelope columns resolve at stamp-time to
    ``story_id=<trigger_id>`` and ``story_id_source=unelected`` (never
    ``fallback_trigger`` — that tag means election ran at schedule).

    On programmer error (missing ``trigger_id``), raises ``ValueError``.
    """
    envelope = _envelope_from_row(row)
    payload["story_id"] = envelope.story_id
    payload["story_id_source"] = envelope.story_id_source
    payload["asked_by"] = envelope.asked_by
    payload["purpose"] = envelope.purpose
    payload["purpose_source"] = envelope.purpose_source
    if row.execution_id:
        payload.setdefault("execution_id", row.execution_id)
    if row.terminal_status:
        payload.setdefault("terminal_status", row.terminal_status)
    if row.archive_uri:
        payload.setdefault("archive_uri", row.archive_uri)
    if row.arc:
        payload.setdefault("arc", row.arc)
    payload.setdefault("trigger_id", row.id)
    return payload


def degraded_sentinel_envelope(row: TriggerRow) -> dict[str, str]:
    """S-B6 sentinel when stamp raises — emit continues with witness keys."""
    tid = _require_trigger_id(row.id)
    return {
        "story_id": tid,
        "story_id_source": "unelected",
        "asked_by": row.created_by.strip() or "unknown",
        "purpose": PURPOSE_UNSTATED,
        "purpose_source": "unstated",
    }


def emit_trigger_signal(
    signal: str,
    row: TriggerRow,
    *,
    publish: Any,
    **extra: Any,
) -> None:
    """Stamp envelope then publish; degrade-not-drop on stamp failure (S-B6)."""
    global _stamp_degrade_count
    payload: dict[str, Any] = dict(extra)
    try:
        stamp_trigger_envelope(payload, row)
    except Exception:
        _stamp_degrade_count += 1
        logger.warning(
            "trigger envelope stamp degraded signal=%s trigger_id=%s count=%d",
            signal,
            row.id,
            _stamp_degrade_count,
            exc_info=True,
        )
        payload.update(degraded_sentinel_envelope(row))
    publish(signal, payload)

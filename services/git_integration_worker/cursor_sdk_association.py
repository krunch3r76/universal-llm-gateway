"""Association envelope helpers for cursor-sdk dispatch lifecycle emits."""

from __future__ import annotations

from typing import Any

from implement_admission.dispatch_topic import extract_dispatch_topic

from services.git_integration_worker.git_worker_lifecycle_events import (
    request_id_from_dispatch_id,
)
from services.git_integration_worker.models.cursor_api import CursorDispatchRequest


def build_dispatch_association_fields(
    *,
    req: CursorDispatchRequest,
    packet_text: str,
) -> dict[str, str | None]:
    """Build association + topic fields for admit and start emits.

    Uses the same ``build_association_envelope`` inputs as terminal
    ``emit_sdk_worker_completed`` so queued, dispatched, and completed rows share
    one association identity per dispatch. ``purpose`` stays story-wire intent;
    ``topic`` is the operator-facing one-liner.
    """
    from systems.frontier_consult.story_wire import build_association_envelope

    envelope = build_association_envelope(
        purpose_body=packet_text or req.message,
        caller_agent=req.caller_agent,
        request_id=request_id_from_dispatch_id(req.dispatch_id),
        dispatch_id=req.dispatch_id,
        packet_path=req.packet_path,
    )
    from services.git_integration_worker.cursor_sdk_packet import (
        extract_packet_kind_from_packet,
    )

    return {
        "asked_by": envelope.asked_by,
        "purpose": envelope.purpose,
        "story_id": envelope.story_id,
        "topic": extract_dispatch_topic(packet_text or req.message),
        "nest_under": req.nest_under,
        "packet_kind": (
            extract_packet_kind_from_packet(packet_text) if packet_text else None
        ),
    }


def association_from_record_json(record_json: str) -> dict[str, Any]:
    """Recover admit-time fields from persisted ``record_json`` on promote/restart."""
    import json

    try:
        data = json.loads(record_json) if record_json else {}
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, Any] = {}
    for key in ("admitted_via", "message", "packet_path"):
        if data.get(key) is not None:
            out[key] = data[key]
    return out


__all__ = ["build_dispatch_association_fields", "association_from_record_json", "extract_dispatch_topic"]

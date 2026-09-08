"""Plan-mode closeout → implement nest handoff contract (W3 bridge)."""

from __future__ import annotations

import json
from typing import Any

NEST_IMPLEMENT_HINT_KEY = "nest_implement_hint"
_PLAN_VERDICT_COMPLETE = "PLAN_COMPLETE"
_DENSITY_IMPLEMENT_READY = "implement_ready"


def build_nest_implement_hint(
    *,
    plan_verdict: str | None,
    implement_ready: bool,
    thread_id: str,
    dispatch_id: str,
    source_ref: str | None = None,
    artifact_paths: list[str] | None = None,
) -> dict[str, Any] | None:
    """Machine-readable implement handoff when plan leg completes and spec is ready."""
    if plan_verdict != _PLAN_VERDICT_COMPLETE or not implement_ready:
        return None
    hint: dict[str, Any] = {
        "verdict": _PLAN_VERDICT_COMPLETE,
        "density_triage": _DENSITY_IMPLEMENT_READY,
        "thread_id": thread_id,
        "dispatch_id": dispatch_id,
        "contract": "implement",
        "sdk_mode": "agent",
    }
    if source_ref:
        hint["source_ref"] = source_ref
    paths = [p for p in (artifact_paths or []) if p]
    if paths:
        hint["artifact_paths"] = paths
    return hint


def _closeout_payload(body: str | dict[str, Any] | None) -> dict[str, Any] | None:
    if body is None:
        return None
    if isinstance(body, dict):
        return body
    text = body.strip()
    if not text:
        return None
    if not text.startswith("{"):
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def parse_nest_implement_hint(body: str | dict[str, Any] | None) -> dict[str, Any] | None:
    """Extract ``nest_implement_hint`` from ImplementCloseout JSON."""
    payload = _closeout_payload(body)
    if payload is None:
        return None
    raw = payload.get(NEST_IMPLEMENT_HINT_KEY)
    return raw if isinstance(raw, dict) else None


def plan_implement_handoff_eligible(hint: dict[str, Any] | None) -> bool:
    """True when a parsed hint unblocks the implement nest leg."""
    if not hint:
        return False
    if str(hint.get("verdict") or "") != _PLAN_VERDICT_COMPLETE:
        return False
    triage = str(hint.get("density_triage") or "").strip().lower()
    return triage == _DENSITY_IMPLEMENT_READY


def plan_implement_handoff_open(body: str | dict[str, Any] | None) -> bool:
    """Convenience: closeout JSON carries an eligible nest implement hint."""
    return plan_implement_handoff_eligible(parse_nest_implement_hint(body))


__all__ = [
    "NEST_IMPLEMENT_HINT_KEY",
    "build_nest_implement_hint",
    "parse_nest_implement_hint",
    "plan_implement_handoff_eligible",
    "plan_implement_handoff_open",
]

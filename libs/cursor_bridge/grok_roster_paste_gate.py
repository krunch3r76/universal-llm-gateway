"""Grok roster-row gate for display-chat wake paste (a:43821 isRunning slice)."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

from cursor_bridge.grok_roster_js import js_roster_row_gate

_IDLE_TRACKERS: dict[tuple[str, str], float | None] = {}


def _stable_s() -> float:
    raw = os.environ.get("GROK_ROSTER_GATE_STABLE_S", "5").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 5.0


def reset_grok_roster_gate_trackers() -> None:
    """Clear in-process idle clocks (test isolation)."""
    _IDLE_TRACKERS.clear()


@dataclass(frozen=True)
class GrokRosterGateSample:
    """One CDP roster read plus optional surface composer draft."""

    ok: bool
    is_running: bool | None
    is_composing: bool | None
    composer_draft: str
    telemetry: str | None = None

    @classmethod
    def from_cdp(cls, raw: object, *, composer_draft: str = "") -> GrokRosterGateSample:
        if not isinstance(raw, dict):
            return cls(
                ok=False,
                is_running=None,
                is_composing=None,
                composer_draft=composer_draft,
                telemetry="grok_roster_unreadable",
            )
        if not raw.get("ok"):
            code = str(raw.get("code") or "grok_roster_unreadable")
            return cls(
                ok=False,
                is_running=None,
                is_composing=None,
                composer_draft=composer_draft,
                telemetry=code,
            )

        def _tri(key: str) -> bool | None:
            if key not in raw:
                return None
            return bool(raw.get(key))

        is_running = _tri("isRunning")
        is_composing = _tri("isComposingMessage")
        if is_running is None or is_composing is None:
            return cls(
                ok=False,
                is_running=is_running,
                is_composing=is_composing,
                composer_draft=composer_draft,
                telemetry="grok_roster_unreadable",
            )
        return cls(
            ok=True,
            is_running=is_running,
            is_composing=is_composing,
            composer_draft=composer_draft,
        )


def _busy_reason(sample: GrokRosterGateSample) -> str | None:
    if not sample.ok:
        return str(sample.telemetry or "grok_roster_unreadable")
    if sample.is_running:
        return "agent_running"
    if sample.is_composing:
        return "grok_composing"
    if sample.composer_draft.strip():
        return "composer_not_empty"
    return None


def evaluate_grok_roster_gate(
    *,
    display: str,
    agent_id: str,
    sample: GrokRosterGateSample,
    now: float | None = None,
) -> tuple[bool, str | None, dict[str, Any]]:
    """Return (allowed, defer_reason, wake_detail) for one roster snapshot."""
    key = (str(display).strip(), str(agent_id).strip())
    clock = time.monotonic() if now is None else float(now)
    busy = _busy_reason(sample)
    detail: dict[str, Any] = {
        "channel": "dom_paste",
        "generating": bool(sample.is_composing)
        if sample.is_composing is not None
        else None,
        "composer_draft": sample.composer_draft,
        "run_state": (
            "running"
            if sample.is_running
            else ("idle" if sample.is_running is False else None)
        ),
    }
    if busy:
        _IDLE_TRACKERS[key] = None
        if busy in {
            "composer_not_empty",
            "agent_running",
            "grok_composing",
            "grok_roster_unreadable",
        }:
            detail["release_claim_for_retry"] = True
        return False, busy, detail
    stable_s = _stable_s()
    if stable_s <= 0:
        return True, None, detail
    idle_since = _IDLE_TRACKERS.get(key)
    if idle_since is None:
        _IDLE_TRACKERS[key] = clock
        return False, "grok_idle_unstable", {**detail, "release_claim_for_retry": True}
    if clock - idle_since < stable_s:
        return False, "grok_idle_unstable", {**detail, "release_claim_for_retry": True}
    return True, None, detail


async def probe_grok_roster_sample(
    ws_url: str, agent_id: str, *, composer_draft: str = ""
) -> GrokRosterGateSample:
    from cursor_bridge.grok_computer import _cdp_eval

    raw = await _cdp_eval(ws_url, js_roster_row_gate(agent_id), await_promise=True)
    return GrokRosterGateSample.from_cdp(raw, composer_draft=composer_draft)


__all__ = [
    "GrokRosterGateSample",
    "evaluate_grok_roster_gate",
    "probe_grok_roster_sample",
    "reset_grok_roster_gate_trackers",
]

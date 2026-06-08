"""Process-local TTL idempotency store for panel_dispatch (R8).

Mirrors ``response_size_guard`` threading/TTL patterns. Must not import
``mcp_events`` — the MCP tool emits events.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Literal

_TTL_SECONDS: float = float(os.getenv("MCP_PANEL_IDEMPOTENCY_TTL_SECONDS", "600"))
_CAPACITY: int = int(os.getenv("MCP_PANEL_IDEMPOTENCY_CAPACITY", "256"))
_DISABLED: bool = os.getenv("MCP_PANEL_IDEMPOTENCY_DISABLE", "").strip().lower() in {
    "1",
    "true",
    "yes",
}


def disabled() -> bool:
    """Return True when idempotency is disabled via env kill switch."""
    return _DISABLED


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def build_panel_request_fingerprint(
    *,
    messages: list[dict[str, Any]],
    dispatch_thread_id: str,
    disposition: str,
    include_synthesizer: bool,
    system: str,
    source_ref: str | None,
    reasoning_effort: str | None,
    generation_options: dict[str, Any] | None,
    max_tool_turns: int | None,
    timeout_seconds: int | None,
) -> str:
    """SHA256 hex over canonical JSON of admission-cost-determining inputs."""
    payload = {
        "messages": messages,
        "dispatch_thread_id": dispatch_thread_id,
        "disposition": disposition,
        "include_synthesizer": include_synthesizer,
        "system": system,
        "source_ref": source_ref,
        "reasoning_effort": reasoning_effort,
        "generation_options": generation_options,
        "max_tool_turns": max_tool_turns,
        "timeout_seconds": timeout_seconds,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(slots=True)
class _Entry:
    fingerprint: str
    state: Literal["pending", "done"]
    envelope: dict[str, Any] | None
    stored_at: float


@dataclass(slots=True)
class PanelIdemResult:
    kind: Literal["reserved", "hit", "in_flight", "conflict"]
    envelope: dict[str, Any] | None = None
    age_s: float = 0.0


_store: dict[str, _Entry] = {}
_lock = threading.Lock()


def _prune_expired(now: float) -> None:
    expired = [k for k, v in _store.items() if (now - v.stored_at) > _TTL_SECONDS]
    for key in expired:
        del _store[key]


def check_or_reserve(
    panel_request_id: str,
    fingerprint: str,
    *,
    now: float | None = None,
) -> PanelIdemResult:
    """Reserve a pending slot or return hit/in_flight/conflict."""
    now = now if now is not None else time.monotonic()
    with _lock:
        _prune_expired(now)
        entry = _store.get(panel_request_id)
        if entry is None:
            if len(_store) >= _CAPACITY:
                oldest_key = min(_store, key=lambda k: _store[k].stored_at)
                del _store[oldest_key]
            _store[panel_request_id] = _Entry(
                fingerprint=fingerprint,
                state="pending",
                envelope=None,
                stored_at=now,
            )
            return PanelIdemResult(kind="reserved")
        if entry.fingerprint != fingerprint:
            return PanelIdemResult(kind="conflict")
        if entry.state == "done":
            return PanelIdemResult(
                kind="hit",
                envelope=entry.envelope,
                age_s=now - entry.stored_at,
            )
        return PanelIdemResult(kind="in_flight", age_s=now - entry.stored_at)


def commit(
    panel_request_id: str,
    envelope: dict[str, Any],
    *,
    now: float | None = None,
) -> None:
    """Mark a pending reservation done with the committed envelope."""
    with _lock:
        entry = _store.get(panel_request_id)
        if entry is None or entry.state != "pending":
            return
        entry.state = "done"
        entry.envelope = envelope


def release(panel_request_id: str) -> None:
    """Delete a pending reservation (no-op if absent or already done)."""
    with _lock:
        entry = _store.get(panel_request_id)
        if entry is None or entry.state != "pending":
            return
        del _store[panel_request_id]

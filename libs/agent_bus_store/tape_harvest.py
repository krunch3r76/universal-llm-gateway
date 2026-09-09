"""Tape harvest orchestration — cortex seal batch then render (AMEND-R D2/D3)."""

from __future__ import annotations

import json
from typing import Any

import httpx
from transport_utils import DEFAULT_CORTEX_URL, make_sync_client

from cortex_store.transcript_cp_anchors import explicit_uuids_for_lane

from .tape_render import render_tape

# Read-path harvest must quick-fail; lid-close seal may take longer.
TAPE_HARVEST_TIMEOUT_S = 8.0
LID_CLOSE_HARVEST_TIMEOUT_S = 120.0


def _call_transcript_harvest(
    *,
    thread_id: str,
    explicit_ids: list[str],
    max_seals: int,
    timeout: float = TAPE_HARVEST_TIMEOUT_S,
) -> dict[str, Any]:
    body = {
        "tool": "transcript_harvest",
        "arguments": json.dumps(
            {
                "thread": thread_id,
                "explicit_transcript_ids": explicit_ids,
                "max_seals": max_seals,
            }
        ),
    }
    try:
        with make_sync_client(DEFAULT_CORTEX_URL, timeout=timeout) as client:
            response = client.post("/dispatch", json=body)
    except httpx.TimeoutException:
        return {
            "error": f"transcript_harvest timed out after {timeout}s",
            "reason": "harvest_timeout",
        }
    except httpx.HTTPError as exc:
        return {
            "error": f"transcript_harvest request failed: {exc}",
            "reason": "harvest_unreachable",
        }
    if response.status_code >= 400:
        return {
            "error": f"cortex-api harvest failed: HTTP {response.status_code}",
            "detail": response.text[:500],
            "reason": "harvest_http_error",
        }
    payload = response.json()
    return payload if isinstance(payload, dict) else {"error": "invalid harvest response"}


def request_lid_close_seal(
    *,
    thread_id: str,
    explicit_transcript_ids: list[str],
) -> dict[str, Any]:
    """O15 D2 lid-close: seal up to one bindable window for explicit ids."""
    return _call_transcript_harvest(
        thread_id=thread_id,
        explicit_ids=explicit_transcript_ids,
        max_seals=1,
        timeout=LID_CLOSE_HARVEST_TIMEOUT_S,
    )


def render_tape_with_harvest(
    *,
    thread_id: str,
    budget_bytes: int,
    harvest: bool = False,
    max_seals: int = 8,
    scope: str = "last_session",
    include_extras: bool = False,
    tools: str = "none",
    harvest_timeout: float = TAPE_HARVEST_TIMEOUT_S,
) -> dict[str, Any]:
    """Optionally harvest bindable windows, then render the continuity tape."""
    harvest_stats: dict[str, Any] | None = None
    if harvest:
        explicit = sorted(explicit_uuids_for_lane(thread_id, set()))
        harvest_result = _call_transcript_harvest(
            thread_id=thread_id,
            explicit_ids=explicit,
            max_seals=max_seals,
            timeout=harvest_timeout,
        )
        if harvest_result.get("error"):
            return harvest_result
        harvest_stats = {
            "discovered": int(harvest_result.get("discovered") or 0),
            "sealed": int(harvest_result.get("sealed") or 0),
            "deferred": int(harvest_result.get("deferred_count") or 0),
            "refused": int(harvest_result.get("refused") or 0),
        }
        from cortex_store.events_tape import agent_bus_tape_harvest_rendered

        agent_bus_tape_harvest_rendered(
            thread_id=thread_id,
            discovered=harvest_stats["discovered"],
            sealed=harvest_stats["sealed"],
            deferred=harvest_stats["deferred"],
        )
    return render_tape(
        thread_id=thread_id,
        budget_bytes=budget_bytes,
        harvest_stats=harvest_stats,
        scope=scope,
        include_extras=include_extras,
        tools=tools,  # type: ignore[arg-type]
    )


__all__ = [
    "LID_CLOSE_HARVEST_TIMEOUT_S",
    "TAPE_HARVEST_TIMEOUT_S",
    "render_tape_with_harvest",
    "request_lid_close_seal",
]

"""Tape harvest orchestration — cortex seal batch then render (AMEND-R D2/D3)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any, TypedDict

import httpx
from cortex_store.transcript_cp_anchors import explicit_uuids_for_lane
from transport_utils import DEFAULT_CORTEX_URL, make_sync_client

from .tape_render import render_tape

# Read-path harvest must quick-fail; lid-close seal may take longer.
TAPE_HARVEST_TIMEOUT_S = 8.0
LID_CLOSE_HARVEST_TIMEOUT_S = 120.0


class _HarvestDecision(TypedDict):
    should_run: bool
    reason: str | None
    targets: list[str]


def pre_pour_harvest_decision(
    *,
    surface: str | None,
    transcript_id: str | None,
    anchors_provider: Callable[[], set[str]],
) -> _HarvestDecision:
    """Decide whether pre-pour harvest runs (cursor surface + uuid + lane anchors)."""
    if surface != "cursor":
        reason = (
            "surface_claude_ai"
            if surface == "claude_ai"
            else "surface_unspecified"
        )
        return {"should_run": False, "reason": reason, "targets": []}
    tid = (transcript_id or "").strip()
    if not tid:
        return {"should_run": False, "reason": "no_transcript_id", "targets": []}
    try:
        uuid.UUID(tid)
    except ValueError:
        return {
            "should_run": False,
            "reason": "transcript_id_not_uuid",
            "targets": [],
        }
    targets = sorted(anchors_provider())
    if not targets:
        return {"should_run": False, "reason": "no_lane_anchors", "targets": []}
    return {"should_run": True, "reason": None, "targets": targets}


def _empty_harvest_record(
    *,
    surface: str | None,
    transcript_id: str | None,
    outcome: str,
    reason: str | None,
    targets: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "outcome": outcome,
        "reason": reason,
        "surface": surface,
        "transcript_id": transcript_id,
        "targets": targets or [],
        "discovered": 0,
        "sealed": 0,
        "deferred": 0,
        "refused": 0,
        "quiescent": 0,
    }


def harvest_before_pour(
    thread_id: str,
    *,
    surface: str | None,
    transcript_id: str | None,
) -> dict[str, Any]:
    """Run transcript_harvest before resume pour when the cursor predicate passes."""
    from .events.resume_fence import emit_resume_fence_harvest_decided

    decision = pre_pour_harvest_decision(
        surface=surface,
        transcript_id=transcript_id,
        anchors_provider=lambda: explicit_uuids_for_lane(thread_id, set()),
    )
    if not decision["should_run"]:
        record = _empty_harvest_record(
            surface=surface,
            transcript_id=transcript_id,
            outcome="skipped",
            reason=decision["reason"],
        )
        emit_resume_fence_harvest_decided(
            thread_id=thread_id,
            transcript_id=transcript_id,
            surface=surface,
            outcome=record["outcome"],
            reason=record["reason"],
            discovered=0,
            sealed=0,
            refused=0,
        )
        return record

    targets = decision["targets"]
    harvest_result = _call_transcript_harvest(
        thread_id=thread_id,
        explicit_ids=targets,
        max_seals=8,
        timeout=TAPE_HARVEST_TIMEOUT_S,
    )
    if harvest_result.get("error"):
        record = _empty_harvest_record(
            surface=surface,
            transcript_id=transcript_id,
            outcome="failed",
            reason=str(harvest_result.get("reason") or "harvest_error"),
            targets=targets,
        )
        emit_resume_fence_harvest_decided(
            thread_id=thread_id,
            transcript_id=transcript_id,
            surface=surface,
            outcome=record["outcome"],
            reason=record["reason"],
            discovered=0,
            sealed=0,
            refused=0,
        )
        return record

    record = {
        "outcome": "ran",
        "reason": None,
        "surface": surface,
        "transcript_id": transcript_id,
        "targets": targets,
        "discovered": int(harvest_result.get("discovered") or 0),
        "sealed": int(harvest_result.get("sealed") or 0),
        "deferred": int(harvest_result.get("deferred_count") or 0),
        "refused": int(harvest_result.get("refused") or 0),
        "quiescent": int(harvest_result.get("quiescent") or 0),
    }
    emit_resume_fence_harvest_decided(
        thread_id=thread_id,
        transcript_id=transcript_id,
        surface=surface,
        outcome=record["outcome"],
        reason=record["reason"],
        discovered=record["discovered"],
        sealed=record["sealed"],
        refused=record["refused"],
    )
    return record


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


def _explicit_ids_for_harvest(
    *,
    thread_id: str,
    scope: str,
    transcript_id: str | None,
) -> list[str]:
    if scope == "window" and transcript_id:
        return [transcript_id]
    return sorted(explicit_uuids_for_lane(thread_id, set()))


def render_tape_with_harvest(
    *,
    thread_id: str,
    budget_bytes: int,
    harvest: bool = False,
    max_seals: int = 8,
    scope: str = "last_session",
    transcript_id: str | None = None,
    prior_cells: int = 1,
    include_extras: bool = False,
    tools: str = "none",
    harvest_timeout: float = TAPE_HARVEST_TIMEOUT_S,
    channel: str = "continuity",
    budget_source: str | None = None,
) -> dict[str, Any]:
    """Optionally harvest bindable windows, then render the continuity tape."""
    harvest_stats: dict[str, Any] | None = None
    if harvest:
        explicit = _explicit_ids_for_harvest(
            thread_id=thread_id,
            scope=scope,
            transcript_id=transcript_id,
        )
        harvest_result = _call_transcript_harvest(
            thread_id=thread_id,
            explicit_ids=explicit,
            max_seals=max_seals,
            timeout=harvest_timeout,
        )
        if harvest_result.get("error"):
            harvest_stats = {
                "error": harvest_result.get("error"),
                "reason": harvest_result.get("reason", "harvest_error"),
            }
            from cortex_store.events_tape import agent_bus_tape_harvest_failed

            agent_bus_tape_harvest_failed(
                thread_id=thread_id,
                reason=str(harvest_stats["reason"]),
            )
        else:
            harvest_stats = {
                "discovered": int(harvest_result.get("discovered") or 0),
                "sealed": int(harvest_result.get("sealed") or 0),
                "deferred": int(harvest_result.get("deferred_count") or 0),
                "refused": int(harvest_result.get("refused") or 0),
                "quiescent": int(harvest_result.get("quiescent") or 0),
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
        transcript_id=transcript_id,
        prior_cells=prior_cells,
        include_extras=include_extras,
        tools=tools,  # type: ignore[arg-type]
        channel=channel,
        budget_source=budget_source,
    )


__all__ = [
    "LID_CLOSE_HARVEST_TIMEOUT_S",
    "TAPE_HARVEST_TIMEOUT_S",
    "harvest_before_pour",
    "pre_pour_harvest_decision",
    "render_tape_with_harvest",
    "request_lid_close_seal",
]

"""Charter-runner periodic sweep — enqueue missed ``consolidate-continuity`` runs.

Scans active ``role:root`` houses whose hub WATERMARK lags behind a CLOSEOUT
on the root or any depth-1 child lane, then dispatches through the same
debounced path as ``continuity_consolidate_trigger`` (not a replay script).

Kill switch: ``AGENT_BUS_CONTINUITY_SWEEP=0``. Dry-run logs candidates only when
``CONTINUITY_SWEEP_DRY_RUN=1``. Rate limit: ``CONTINUITY_SWEEP_MAX_ROOTS`` (default 5).

Spec: ``cortex://notes/system/specs/continuity-consolidate-pipeline.md`` § periodic sweep.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from transport_utils import DEFAULT_CORTEX_URL, make_sync_client

from .continuity_consolidate_trigger import (
    ROOT_TAG,
    _schedule_debounced,
    consolidation_enabled,
    is_closeout_subject,
    resolve_root,
)
from .db.lane_associations import list_substantiated_child_thread_ids
from .db.threads import list_threads_v2
from .db.turns import get_turns

log = logging.getLogger("agent_bus.continuity_sweep")

SEEDED_BY = "continuity-consolidate"
WATERMARK_PREFIX = "WATERMARK: consolidated_through="
_WATERMARK_RE = re.compile(
    r"consolidated_through=(?P<thread>[0-9a-zA-Z_-]+)#(?P<turn>\d+)"
)
_CORTEX_TIMEOUT_S = 15.0
_SCAN_WINDOW = 120
_MAX_ROOTS_DEFAULT = 5


def sweep_enabled() -> bool:
    """Kill switch — ``AGENT_BUS_CONTINUITY_SWEEP=0`` disables the charter leg."""
    return os.environ.get("AGENT_BUS_CONTINUITY_SWEEP", "1") not in {
        "0",
        "false",
        "no",
    }


def dry_run_enabled() -> bool:
    return os.environ.get("CONTINUITY_SWEEP_DRY_RUN", "0") in {"1", "true", "yes"}


def max_roots_per_tick() -> int:
    raw = os.environ.get("CONTINUITY_SWEEP_MAX_ROOTS", "")
    if not raw:
        return _MAX_ROOTS_DEFAULT
    try:
        return max(1, int(raw))
    except ValueError:
        return _MAX_ROOTS_DEFAULT


def hub_entity_id(root_thread: str) -> str:
    return f"document:{root_thread}-continuity"


def parse_watermark(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Newest pipeline-authored WATERMARK row, decoded."""
    for row in sorted(rows, key=lambda r: int(r.get("id") or 0), reverse=True):
        if row.get("seeded_by") != SEEDED_BY:
            continue
        claim = str(row.get("claim") or "")
        match = _WATERMARK_RE.search(claim)
        if claim.startswith(WATERMARK_PREFIX) and match:
            return {
                "assertion_id": row.get("id"),
                "thread": match.group("thread"),
                "turn": int(match.group("turn")),
                "claim": claim,
            }
    return None


def _trigger_is_stale(
    trigger: dict[str, Any], watermark: dict[str, Any] | None
) -> bool:
    if watermark is None:
        return False
    same_lane = str(watermark.get("thread")) == str(trigger.get("thread"))
    return same_lane and int(watermark.get("turn") or 0) >= int(
        trigger.get("turn") or 0
    )


def _cortex_dispatch(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    with make_sync_client(DEFAULT_CORTEX_URL, timeout=_CORTEX_TIMEOUT_S) as client:
        resp = client.post("/dispatch", json={"tool": tool, "arguments": arguments})
        resp.raise_for_status()
        body = resp.json()
    return body if isinstance(body, dict) else {"error": "non_object_response"}


def fetch_hub_watermark(root: str) -> dict[str, Any] | None:
    """Read the hub WATERMARK for ``root``; None when hub missing or unreadable."""
    entity_id = hub_entity_id(root)
    reply = _cortex_dispatch(
        "assertions",
        {"entity_id": entity_id, "superseded": False, "limit": 100, "intent": "full"},
    )
    if "error" in reply:
        log.debug(
            "continuity sweep: hub assertions unreadable root=%s err=%s",
            root,
            reply.get("error"),
        )
        return None
    rows = reply.get("assertions") or reply.get("items") or []
    assertions = [r for r in rows if isinstance(r, dict)]
    return parse_watermark(assertions)


def _threads_to_scan(root: str) -> tuple[str, ...]:
    child_ids = list_substantiated_child_thread_ids(parent_thread_id=root)
    return (root, *child_ids)


def find_stale_closeout(
    root: str, watermark: dict[str, Any] | None
) -> tuple[str, int] | None:
    """Return ``(trigger_thread, turn_number)`` for the newest unstale CLOSEOUT."""
    best: tuple[int, str, int] | None = None
    for thread_id in _threads_to_scan(root):
        turns = get_turns(thread=thread_id, last=_SCAN_WINDOW)
        for turn in turns:
            if not is_closeout_subject(turn.get("subject")):
                continue
            turn_number = int(turn.get("turn_number") or 0)
            trigger = {"thread": thread_id, "turn": turn_number}
            if _trigger_is_stale(trigger, watermark):
                continue
            if best is None or turn_number > best[0]:
                best = (turn_number, thread_id, turn_number)
    if best is None:
        return None
    return best[1], best[2]


def sweep_root(root: str) -> dict[str, Any] | None:
    """Evaluate one root; enqueue or log a dry-run candidate. Returns action record."""
    if resolve_root(root) != root:
        return None
    watermark = fetch_hub_watermark(root)
    stale = find_stale_closeout(root, watermark)
    if stale is None:
        return None
    trigger_thread, turn_number = stale
    record = {
        "root": root,
        "trigger_thread": trigger_thread,
        "turn": turn_number,
        "watermark": watermark,
        "dry_run": dry_run_enabled(),
    }
    if dry_run_enabled():
        log.info(
            "continuity sweep dry-run root=%s would enqueue trigger=%s#%s watermark=%s",
            root,
            trigger_thread,
            turn_number,
            watermark,
        )
        return record
    _schedule_debounced(root, trigger_thread, turn_number)
    log.info(
        "continuity sweep enqueued root=%s trigger=%s#%s",
        root,
        trigger_thread,
        turn_number,
    )
    return record


def list_role_root_ids(*, limit: int | None = None) -> list[str]:
    rows = list_threads_v2(status="active", tags=[ROOT_TAG], limit=limit)
    return [str(row["id"]) for row in rows if row.get("id")]


def run_continuity_sweep() -> list[dict[str, Any]]:
    """Scan up to ``max_roots_per_tick()`` active roots; return action records."""
    if not sweep_enabled():
        log.debug("continuity sweep disabled (AGENT_BUS_CONTINUITY_SWEEP=0)")
        return []
    if not consolidation_enabled():
        log.debug("continuity sweep skipped (AGENT_BUS_CONTINUITY_CONSOLIDATE=0)")
        return []
    cap = max_roots_per_tick()
    root_ids = list_role_root_ids(limit=cap)
    results: list[dict[str, Any]] = []
    for root in root_ids:
        try:
            record = sweep_root(root)
        except Exception:  # noqa: BLE001 — one root must not abort the batch
            log.warning("continuity sweep failed root=%s", root, exc_info=True)
            continue
        if record is not None:
            results.append(record)
    return results


__all__ = [
    "dry_run_enabled",
    "fetch_hub_watermark",
    "find_stale_closeout",
    "hub_entity_id",
    "list_role_root_ids",
    "max_roots_per_tick",
    "parse_watermark",
    "run_continuity_sweep",
    "sweep_enabled",
    "sweep_root",
]

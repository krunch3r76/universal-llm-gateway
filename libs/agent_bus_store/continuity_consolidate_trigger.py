"""CLOSEOUT → ``consolidate-continuity`` pipeline trigger (AMEND-A continuous consolidation).

Every CLOSEOUT-subject turn landing on a ``role:root`` house — or on a lane
whose current parent is one — enqueues one asynchronous Stargate pipeline
run that folds the closeout into the Cortex graph. The bus stays the trigger
and the transport only: the pipeline never reads the bus back (the gateway
container carries no bus token), so everything the job needs is pre-fetched
here and travels as ``pipeline_options``.

Non-blocking by construction: the turn insert returns before this module does
any I/O; the fetch + HTTP dispatch run on a daemon thread and never raise into
the caller. Serialisation per root house is the pipeline's concurrency block
keyed on ``dispatch_thread_id`` (= the root), not a lock here.

Spec: ``cortex://notes/system/specs/continuity-consolidate-pipeline.md``.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from .checkpoint_projection import extract_authored_residue
from .db.lane_associations import get_current_lane
from .db.threads import get_thread
from .db.turns import get_turn_by_number, get_turns

log = logging.getLogger("agent_bus.continuity_consolidate")

PIPELINE_ID = "consolidate-continuity"
CALLER_AGENT = "agent-bus:continuity-consolidate"
ROOT_TAG = "role:root"
_CLOSEOUT_PREFIX = "closeout"
_CHECKPOINT_PREFIX = "checkpoint"
_TIP_SCAN_WINDOW = 60
_TRIGGER_BODY_CAP = 6000
_RESIDUE_CAP = 4000
_DISPATCH_TIMEOUT_S = 10.0


def consolidation_enabled() -> bool:
    """Kill switch — ``AGENT_BUS_CONTINUITY_CONSOLIDATE=0`` disables the hook."""
    return os.environ.get("AGENT_BUS_CONTINUITY_CONSOLIDATE", "1") not in {
        "0",
        "false",
        "no",
    }


def is_closeout_subject(subject: str | None) -> bool:
    return (subject or "").strip().lower().startswith(_CLOSEOUT_PREFIX)


def is_checkpoint_subject(subject: str | None) -> bool:
    return (subject or "").strip().lower().startswith(_CHECKPOINT_PREFIX)


def _is_root(thread: dict[str, Any] | None) -> bool:
    return bool(thread) and ROOT_TAG in (thread.get("tags") or [])


def resolve_root(thread_id: str) -> str | None:
    """Return the root house a closeout on ``thread_id`` consolidates into.

    A ``role:root`` thread is its own root. Otherwise the current lane parent
    is consulted exactly one level up — lanes hang directly off their house in
    the continuity-lane design, so deeper walks would only mask a mis-bound lane.
    """
    thread = get_thread(thread_id)
    if _is_root(thread):
        return thread_id
    try:
        lane = get_current_lane(thread_id=thread_id)
    except LookupError:
        return None
    parent = lane.get("parent_thread")
    if parent and _is_root(get_thread(parent)):
        return parent
    return None


def _tip_checkpoint(root: str) -> dict[str, Any] | None:
    """Newest CHECKPOINT-subject turn on the root, authored residue only."""
    turns = get_turns(thread=root, last=_TIP_SCAN_WINDOW)
    for turn in reversed(turns):
        if is_checkpoint_subject(turn.get("subject")):
            return {
                "turn": turn.get("turn_number"),
                "from_agent": turn.get("from_agent"),
                "created_at": turn.get("created_at"),
                "subject": turn.get("subject"),
                "residue": extract_authored_residue(turn.get("body") or "")[
                    :_RESIDUE_CAP
                ],
            }
    return None


def _trigger_turn(thread_id: str, turn_number: int) -> dict[str, Any] | None:
    turn = get_turn_by_number(thread_id, turn_number)
    if turn is None:
        return None
    return {
        "thread": thread_id,
        "turn": turn_number,
        "from_agent": turn.get("from_agent"),
        "created_at": turn.get("created_at"),
        "subject": turn.get("subject"),
        "body": (turn.get("body") or "")[:_TRIGGER_BODY_CAP],
    }


def build_consolidate_options(
    *, root: str, trigger_thread: str, turn_number: int
) -> dict[str, Any]:
    """Assemble the ``pipeline_options`` payload — the job's entire view of the bus."""
    root_row = get_thread(root) or {}
    return {
        "root_thread": root,
        "root": {
            "slug": root_row.get("slug"),
            "summary": root_row.get("summary"),
            "tags": root_row.get("tags") or [],
            "turn_count": root_row.get("turn_count"),
        },
        "tip_checkpoint": _tip_checkpoint(root),
        "trigger": _trigger_turn(trigger_thread, turn_number),
    }


def enqueue_consolidate(options: dict[str, Any]) -> str | None:
    """Fire-and-forget ``POST /api/v1/pipelines/dispatch``; returns the execution id."""
    from transport_utils import DEFAULT_STARGATE_URL, make_sync_client

    root = options["root_thread"]
    trigger = options.get("trigger") or {}
    body = {
        "model": PIPELINE_ID,
        "messages": [
            {
                "role": "user",
                "content": f"consolidate {root} after {trigger.get('thread')}#{trigger.get('turn')}",
            }
        ],
        "pipeline_options": options,
        "dispatch_thread_id": root,
        "caller_agent": CALLER_AGENT,
    }
    with make_sync_client(DEFAULT_STARGATE_URL, timeout=_DISPATCH_TIMEOUT_S) as client:
        resp = client.post("/api/v1/pipelines/dispatch", json=body)
    resp.raise_for_status()
    execution_id = resp.json().get("execution_id")
    log.info(
        "continuity consolidate enqueued root=%s trigger=%s#%s execution_id=%s",
        root,
        trigger.get("thread"),
        trigger.get("turn"),
        execution_id,
    )
    return execution_id


def _run(thread_id: str, turn_number: int) -> None:
    try:
        root = resolve_root(thread_id)
        if root is None:
            return
        options = build_consolidate_options(
            root=root, trigger_thread=thread_id, turn_number=turn_number
        )
        if options.get("trigger") is None:
            log.warning(
                "continuity consolidate: trigger %s#%s not readable, no dispatch",
                thread_id,
                turn_number,
            )
            return
        enqueue_consolidate(options)
    except Exception:  # noqa: BLE001 — background hygiene must never surface into the bus
        log.warning(
            "continuity consolidate trigger failed thread=%s turn=%s",
            thread_id,
            turn_number,
            exc_info=True,
        )


def maybe_enqueue_continuity_consolidate(
    *, thread_id: str, turn_number: int, subject: str | None
) -> bool:
    """Schedule consolidation for a freshly inserted turn; True when a job thread was started.

    Cheap gates (kill switch, subject grammar) run inline; root resolution needs
    DB reads and is deferred to the worker so ``insert_turn`` pays nothing.
    """
    if not consolidation_enabled() or not is_closeout_subject(subject):
        return False
    worker = threading.Thread(
        target=_run,
        args=(thread_id, turn_number),
        name=f"continuity-consolidate-{thread_id}-{turn_number}",
        daemon=True,
    )
    worker.start()
    return True

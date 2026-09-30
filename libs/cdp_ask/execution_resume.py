"""Resume in-flight executions from their registry rows — after a recycle or a Chrome death.

Two triggers, one path. ``boot``: the new cdp_ask process reads
``in_flight_rows(active.json)``, adopts each row's execution into the store
*before* ``boot_reconcile`` (so the host is refused as ``already_live_execution``
and kept), then re-attaches. ``host_lost``: the Chrome under a live turn died
(a:36969); the finish ladder parked the row dormant and hands the record here.

Re-attach is by ``chat_url`` — the Cowork session outlives both the process
and the tab — through ``ensure_cse_attached``: a dormant seat bound to the URL
relaunches on its own profile; a live lane that already shows the URL is
reused. The turn is then harvested until the page stops streaming, archived,
and the execution terminalizes through the same store transitions a first-run
execution uses. Receipts are ``cdp_ask.execution.resumed`` and the row's
``execution_state``; the resume writes no provenance episode.

The original request body is not on the row, so harvest runs with
``HarvestRequest`` defaults (todo:cdp-ask-durable-execution-state a:36964).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from typing import Any

from claude_bundles.cdp_registry.execution_state import in_flight_rows
from claude_bundles.cdp_registry.models import _row_to_registration
from claude_bundles.cdp_registry_store import load_active
from claude_bundles.chat_model_select import current_model_label
from claude_bundles.cse_wake_retain import registration_has_wake_debt
from claude_bundles.project_ask import archive_harvest
from claude_bundles.project_ask_abort import deregister_on_exit

from cdp_ask.cse_session_harvest import harvest_page
from cdp_ask.cse_session_models import HarvestRequest
from cdp_ask.execution_store import ExecutionRecord, ExecutionStore
from cdp_ask.followup_events import (
    cdp_ask_execution_resume_failed,
    cdp_ask_execution_resumed,
)
from cdp_ask.followup_events import emit as emit_event
from cdp_ask.runner import verify_harvest_root

logger = logging.getLogger(__name__)

# Streaming poll cadence and ceiling. Long turns run tens of minutes; the
# execution TTL (7200 s) is the outer bound the reaper still enforces.
RESUME_POLL_S = 10.0
RESUME_WAIT_S = 3600.0
_RESUMABLE_ROW_STATUSES = frozenset({"active", "dormant", "orphaned_alive", "retained"})

__all__ = ["hydrate_in_flight", "resume_execution", "spawn_resume"]


async def hydrate_in_flight(store: ExecutionStore) -> list[ExecutionRecord]:
    """Adopt every in-flight execution row into *store*; return the adopted records.

    Followup-kind entries are seat-busy markers with nothing to harvest through
    the store, so they gate the drain but are not adopted.
    """
    active = await asyncio.to_thread(load_active)
    adopted: list[ExecutionRecord] = []
    for rid, row in in_flight_rows(active).items():
        entry = row["execution_state"]
        if entry.get("kind") == "followup":
            continue
        if str(row.get("status") or "") not in _RESUMABLE_ROW_STATUSES:
            continue
        record = await store.adopt(
            execution_id=str(entry["execution_id"]),
            registration_id=rid,
            holder=str(row.get("holder") or "cdp-ask-satellite"),
            purpose=row.get("purpose"),
            parent_thread=row.get("parent_thread"),
            mission_kind=row.get("mission_kind"),
            started_at=float(entry["started_at"]),
            durable_state=str(entry["state"]),
        )
        adopted.append(record)
    return adopted


async def spawn_resume(
    store: ExecutionStore, record: ExecutionRecord, *, trigger: str
) -> asyncio.Task[None]:
    """Run ``resume_execution`` as the record's guarded task so abort/TTL can cancel it."""
    from cdp_ask.execution_ladder import guard_execution

    async def _body() -> None:
        await resume_execution(store, record, trigger=trigger)

    task = asyncio.create_task(guard_execution(store, record.execution_id, _body))
    await store.attach_task(record.execution_id, task)
    return task


async def _fail(
    store: ExecutionStore,
    record: ExecutionRecord,
    *,
    trigger: str,
    chat_url: str | None,
    code: str,
) -> None:
    emit_event(
        cdp_ask_execution_resume_failed(
            execution_id=record.execution_id,
            registration_id=record.registration_id,
            chat_url=chat_url,
            trigger=trigger,
            error_code=code,
        )
    )
    await store.mark_terminal(
        record.execution_id,
        status="failed",
        error=f"resume failed: {code}",
        stall_stage="mark_terminal",
    )


def _row_for(registration_id: str | None) -> dict[str, Any] | None:
    if not registration_id:
        return None
    return load_active().get(registration_id)


def _resume_archive_path(execution_id: str) -> str:
    root = verify_harvest_root()
    return str(
        root / "notes/system/threads" / f"cdp-ask-archive-cdp-resume-{execution_id}.md"
    )


def _body_of(response: Any) -> str:
    from cdp_ask.poll_recovery import _body_from_harvest

    return _body_from_harvest(response)


_CSE_ID = re.compile(r"/cowork/(cse_[A-Za-z0-9]+)")
# ExecutionPollResponse.harvest_provenance literal; the CSE harvester's own
# labels (cse-dom, metadata_only) are DOM-level and read as "chat" to a poller.
_POLL_PROVENANCE = {"output-file", "cortex-uri", "chat", "chat-large", "artifact-card"}


def _poll_provenance(content_provenance: str | None) -> str:
    value = str(content_provenance or "")
    return value if value in _POLL_PROVENANCE else "chat"


def _cse_id(url: str) -> str:
    """Session identity of a Cowork URL — the only part the attach must match."""
    match = _CSE_ID.search(url or "")
    return match.group(1) if match else ""


async def resume_execution(
    store: ExecutionStore, record: ExecutionRecord, *, trigger: str
) -> None:
    """Re-attach *record* to its Cowork session, harvest to completion, terminalize."""
    from cdp_ask.cse_session_harvest_open import _teardown_opened
    from cdp_ask.followup_reattach import ensure_cse_attached

    row = await asyncio.to_thread(_row_for, record.registration_id)
    chat_url = str((row or {}).get("chat_url") or "").strip()
    if not chat_url:
        await _fail(store, record, trigger=trigger, chat_url=None, code="no_chat_url")
        return

    try:
        outcome = await ensure_cse_attached(
            chat_url,
            holder=record.holder or "cdp-ask-satellite",
            purpose=record.purpose,
            parent_thread=record.parent_thread,
            mission_kind=record.mission_kind,
            allow_mint=True,
        )
    except Exception as exc:  # noqa: BLE001 — attach failure is a resume failure
        logger.warning("resume %s attach raised", record.execution_id, exc_info=True)
        await _fail(
            store,
            record,
            trigger=trigger,
            chat_url=chat_url,
            code=f"attach_raised:{type(exc).__name__}",
        )
        return
    if not outcome.ok or outcome.page is None:
        await _fail(
            store,
            record,
            trigger=trigger,
            chat_url=chat_url,
            code=outcome.error or "attach_failed",
        )
        return

    attached_url = str(getattr(outcome.page, "url", "") or "")
    if _cse_id(attached_url) != _cse_id(chat_url):
        # The relaunched host showed another session (restored tab / redirect):
        # harvesting it would archive foreign content under this execution.
        with contextlib.suppress(Exception):
            await _teardown_opened(outcome)
        await _fail(
            store,
            record,
            trigger=trigger,
            chat_url=chat_url,
            code="attach_url_mismatch",
        )
        return

    new_rid = outcome.registration_id
    if new_rid and new_rid != record.registration_id:
        await _transfer_row(store, record, new_rid)
    await store._write_through(record, "streaming", reason=f"resumed:{trigger}")
    emit_event(
        cdp_ask_execution_resumed(
            execution_id=record.execution_id,
            registration_id=record.registration_id,
            chat_url=chat_url,
            trigger=trigger,
            relaunched=bool(outcome.relaunched),
        )
    )

    req = HarvestRequest(
        chat_url=chat_url, execution_id=record.execution_id, source="auto", limit=50
    )
    try:
        response = await _harvest_until_idle(store, record, outcome.page, req)
        if response is None:
            await _fail(
                store, record, trigger=trigger, chat_url=chat_url, code="resume_timeout"
            )
            return
        body = _body_of(response)
        if not body:
            await _fail(
                store, record, trigger=trigger, chat_url=chat_url, code="empty_body"
            )
            return
        attested = (await current_model_label(outcome.page)).strip() or None
        try:
            archive_uri: str | None = await asyncio.to_thread(
                archive_harvest,
                body=body,
                url=chat_url,
                project_uuid="",
                model={},
                attested_model=attested,
                archive_path=_resume_archive_path(record.execution_id),
                execution_id=record.execution_id,
                stargate_execution_id=record.stargate_execution_id,
            )
        except Exception:  # noqa: BLE001 — body still reaches the poller
            logger.warning(
                "resume %s archive failed", record.execution_id, exc_info=True
            )
            archive_uri = None
        await store.mark_terminal(
            record.execution_id,
            status="completed",
            result={
                "ok": True,
                "registration_id": record.registration_id,
                "archive_uri": archive_uri,
                "body": body,
                "body_len": len(body),
                "url": chat_url,
                "project_uuid": "",
                "project_url": "",
                "model": {},
                "attested_model": attested,
                "harvest_provenance": _poll_provenance(response.content_provenance),
                "resumed": True,
                "resume_trigger": trigger,
            },
        )
    finally:
        with contextlib.suppress(Exception):
            await _teardown_opened(outcome)
        if not store.shutting_down:
            # Teardown mid-resume keeps the host; the next process resumes again.
            await asyncio.to_thread(_dispose_host, record)


async def _transfer_row(
    store: ExecutionStore, record: ExecutionRecord, new_rid: str
) -> None:
    """Move the durable state when the session was found on another host."""
    from claude_bundles.cdp_registry.execution_state import set_execution_state

    old_rid = record.registration_id
    if old_rid:
        with contextlib.suppress(Exception):
            await asyncio.to_thread(
                set_execution_state,
                old_rid,
                execution_id=record.execution_id,
                state="transferred",
                reason=f"moved:{new_rid}",
            )
    await store.set_registration_id(record.execution_id, new_rid)


async def _harvest_until_idle(
    store: ExecutionStore, record: ExecutionRecord, page: Any, req: HarvestRequest
) -> Any | None:
    """Harvest on a cadence until the page reports not streaming; None on timeout."""
    deadline = time.monotonic() + RESUME_WAIT_S
    while True:
        response = await harvest_page(page, req, provenance={"resume": True})
        streaming = response.streaming
        await store.update_liveness(
            record.execution_id,
            streaming=streaming,
            stop=None,
            tool_pause=None,
            liveness_observed_at=time.time(),
        )
        if (
            response.outcome == "harvested"
            and streaming is not True
            and _body_of(response)
        ):
            return response
        current = await store.get(record.execution_id)
        if current is not None and current.abort_requested:
            raise asyncio.CancelledError
        if time.monotonic() >= deadline:
            return None
        await asyncio.sleep(RESUME_POLL_S)


def _dispose_host(record: ExecutionRecord) -> None:
    """Apply the ordinary exit policy to the resumed host (kill for ask, park otherwise)."""
    row = _row_for(record.registration_id)
    if row is None or row.get("status") != "active":
        return
    if registration_has_wake_debt(str(record.registration_id)):
        return
    with contextlib.suppress(Exception):
        deregister_on_exit(_row_to_registration(row), purpose=record.purpose)

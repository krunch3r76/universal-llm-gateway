"""Non-blocking fire submit + reconcile helpers for trigger service."""

from __future__ import annotations

import os
from typing import Any

import httpx
from cdp_ask.client import CdpAskClient, CdpAskClientError
from cdp_ask.models import SubmitProjectAskRequest
from universal_logging import get_logger

from services.git_integration_worker.events import publish_lib_signal
from services.git_integration_worker.trigger_service.act_verify import (
    verify_act_for_row,
)
from services.git_integration_worker.trigger_service.models import TriggerRow
from services.git_integration_worker.trigger_service.store import TriggerStore
from services.git_integration_worker.trigger_service.story_envelope import (
    emit_trigger_signal,
)

logger = get_logger(__name__)

_HOLDER = "giw-trigger-service"
_TERMINAL_STATUSES = frozenset({"completed", "failed", "aborted"})


def project_ask_configured() -> bool:
    return bool(os.environ.get("PROJECT_ASK_URL", "").strip())


def is_retryable_submit_error(exc: BaseException) -> bool:
    """Transport, 5xx, and lane-busy refusals are retryable submit failures."""
    if isinstance(exc, CdpAskClientError):
        if exc.status_code is None:
            return True
        if exc.status_code >= 500 or exc.status_code in (429, 503):
            return True
        detail = (exc.detail or "").lower()
        if "lane" in detail and ("busy" in detail or "leased" in detail):
            return True
    if isinstance(exc, httpx.RequestError):
        return True
    return False


def lane_available(client: CdpAskClient) -> tuple[bool, str | None]:
    """Probe cdp-ask active-work; treat hard-limit as lane-busy (retryable)."""
    try:
        snap = client._request("GET", "/v1/project-ask/active-work")
    except CdpAskClientError as exc:
        if is_retryable_submit_error(exc):
            return False, f"active-work probe: {exc}"
        raise
    if snap.get("at_hard_limit"):
        return False, "cdp lane at hard limit"
    return True, None


def _emit(signal: str, row: TriggerRow, **payload: Any) -> None:
    emit_trigger_signal(signal, row, publish=publish_lib_signal, **payload)


def _emit_bare(signal: str, **payload: Any) -> None:
    """Non-row signals (.claimed) — stamp optional keys only."""
    publish_lib_signal(signal, payload)


def submit_fire(row: TriggerRow, *, client: CdpAskClient | None = None) -> str:
    """POST operator-proxy execution; returns execution_id. Raises on failure."""
    http = client or CdpAskClient()
    ok, reason = lane_available(http)
    if not ok:
        raise CdpAskClientError(reason or "lane busy", status_code=503)
    body = SubmitProjectAskRequest(
        prompt_uri=row.prompt_uri,
        holder=_HOLDER,
        purpose=row.purpose,
        model=row.model,
        converse=True,
        no_project_uuid=True,
    )
    result = http.submit(body)
    execution_id = result.get("execution_id")
    if not execution_id:
        raise CdpAskClientError(
            "submit response missing execution_id",
            status_code=502,
            detail=str(result)[:400],
        )
    return str(execution_id)


def fire_once(
    store: TriggerStore,
    row: TriggerRow,
    *,
    client: CdpAskClient | None = None,
) -> TriggerRow:
    """Submit one claimed row; never poll — mark fired or schedule retry."""
    _emit_bare(
        "giw.trigger.claimed",
        trigger_id=row.id,
        arc=row.arc,
        fire_at=row.fire_at,
    )
    attempts = row.attempts + 1
    try:
        execution_id = submit_fire(row, client=client)
    except BaseException as exc:
        error = str(exc)[:500]
        if is_retryable_submit_error(exc):
            updated = store.mark_submit_retry(
                row.id,
                error=error,
                attempts=attempts,
                max_attempts=row.max_attempts,
            )
            _emit(
                "giw.trigger.fire_failed",
                updated,
                reason=error,
                attempt=attempts,
                retryable=True,
            )
            return updated
        updated = store.mark_failed(row.id, error=error, attempts=attempts)
        _emit(
            "giw.trigger.fire_failed",
            updated,
            reason=error,
            attempt=attempts,
            retryable=False,
        )
        return updated

    updated = store.mark_fired(row.id, execution_id=execution_id)
    _emit(
        "giw.trigger.fired",
        updated,
        execution_id=execution_id,
        so_what=row.so_what,
    )
    return updated


def reconcile_row(
    store: TriggerStore,
    row: TriggerRow,
    *,
    client: CdpAskClient | None = None,
) -> TriggerRow | None:
    """Poll one fired row to terminal; returns row when terminal reached."""
    if not row.execution_id:
        return None
    http = client or CdpAskClient()
    try:
        poll = http.poll(row.execution_id)
    except CdpAskClientError as exc:
        logger.warning("trigger reconcile poll failed id=%s: %s", row.id, exc)
        return None
    status = str(poll.get("status") or "")
    if status not in _TERMINAL_STATUSES:
        return None
    archive_uri = poll.get("archive_uri")
    error = poll.get("error")
    updated = store.mark_reconciled(
        row.id,
        terminal_status=status,
        archive_uri=str(archive_uri) if archive_uri else None,
        error=str(error) if error else None,
    )
    _emit(
        "giw.trigger.reconciled",
        updated,
        execution_id=row.execution_id,
        terminal_status=status,
        archive_uri=archive_uri,
    )
    act_row = updated
    if status == "completed":
        act_result = verify_act_for_row(
            act_row,
            archive_body=str(poll.get("archive_body") or "") or None,
        )
        act_row = store.set_act_fields(
            act_row.id,
            act_status=act_result["act_status"],
            act_evidence_uri=act_result["act_evidence_uri"],
            act_error=act_result["act_error"],
        )
        event = act_result.get("event")
        if event:
            _emit_bare(event, **act_result["event_payload"])
    return act_row

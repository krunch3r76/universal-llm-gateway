"""Push a stand-down receipt into the predecessor CSE at succession confirm (G2)."""

from __future__ import annotations

from typing import Any

from cdp_ask.client import CdpAskClient, CdpAskClientError
from hop_handoff.body import build_seat_stand_down_body
from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.hop_cadence_events import (
    emit_predecessor_pushed,
)
from services.git_integration_worker.cursor_auto.hop_cadence_predecessor import (
    PredecessorHandle,
    PredecessorVerdict,
)

logger = get_logger(__name__)

PasteOutcome = dict[str, bool]


def push_predecessor_receipt(
    *,
    thread_id: str,
    handle: PredecessorHandle,
    new_registration_id: str,
    matched_execution_id: str,
    client: CdpAskClient | None = None,
) -> PasteOutcome:
    """Paste G1 stand-down body into the recorded predecessor CSE (fail-soft)."""
    if handle.verdict != PredecessorVerdict.INCUMBENT_RECORDED:
        return {"attempted": False, "ok": False}

    target_reg = handle.registration_id.strip()
    if not target_reg:
        return {"attempted": False, "ok": False}

    prompt_text = build_seat_stand_down_body(
        superseded_registration_id=target_reg,
        new_registration_id=new_registration_id.strip(),
        execution_id=matched_execution_id.strip(),
        parent_thread=thread_id,
    )
    idempotency_key = f"hop-cadence-stand-down:{thread_id}:{target_reg}"
    payload: dict[str, Any] = {
        "registration_id": target_reg,
        "prompt_text": prompt_text,
        "envelope": "stand_down",
        "parent_thread": thread_id,
        "idempotency_key": idempotency_key,
    }

    http = client or CdpAskClient()
    error_detail: str | None = None
    try:
        result = http.paste(payload)
        ok = result.get("ok") is True
        if not ok:
            error_detail = str(
                result.get("detail") or result.get("code") or "paste_failed"
            )
    except CdpAskClientError as exc:
        ok = False
        error_detail = str(exc)
        logger.warning(
            "hop_cadence predecessor push failed thread=%s reg=%s err=%s",
            thread_id,
            target_reg,
            exc,
        )

    emit_predecessor_pushed(
        thread_id=thread_id,
        registration_id=target_reg,
        execution_id=handle.execution_id,
        new_registration_id=new_registration_id.strip(),
        idempotency_key=idempotency_key,
        ok=ok,
        error=error_detail,
    )
    return {"attempted": True, "ok": ok}


__all__ = ["PasteOutcome", "push_predecessor_receipt"]

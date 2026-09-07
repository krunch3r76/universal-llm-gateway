"""Conductor hop park path — worker-thread line, event, page, ledger stamp.

Split from ``conductor_hop_budget``: deciding whether a mission may hop and
announcing that it may not are separate concerns, and the budget verdict is
the only thing the reactor consults on the hot path.
"""

from __future__ import annotations

from typing import Any

from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_budget import (
    HOP_PARK_REASON_KEY,
    HOP_PARKED_KEY,
    build_budget_authority_patch,
)

logger = get_logger(__name__)


def build_parked_transport_body(*, reason: str, hop_seq: int | None) -> str:
    lines = [
        "status: complete",
        "stop: PARKED_TRANSPORT",
        f"reason: {reason}",
    ]
    if hop_seq is not None:
        lines.append(f"hop_seq: {hop_seq}")
    return "\n".join(lines) + "\n"


def default_park_poster(thread_id: str, body: str) -> None:
    """POST ``PARKED_TRANSPORT`` closeout shape on the worker thread."""
    import os

    import httpx
    from transport_utils import DEFAULT_AGENT_BUS_URL, make_sync_client

    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    payload = {
        "thread": thread_id,
        "from": "conductor-hop",
        "to": "cursor",
        "subject": f"stop: PARKED_TRANSPORT — {thread_id}",
        "body": body,
        "status": "open",
    }
    with make_sync_client(DEFAULT_AGENT_BUS_URL, timeout=15.0) as client:
        resp = client.post("/turns", json=payload, headers=headers)
    if resp.status_code >= 400:
        raise httpx.HTTPStatusError(
            f"park post failed status={resp.status_code}",
            request=resp.request,
            response=resp,
        )


async def page_hop_budget_parked(
    *,
    dispatch_id: str,
    thread_id: str,
    reason: str,
    work_key: str,
) -> bool:
    """Awareness page when hop budgets exhaust (bind §2.6.6 / §6)."""
    from pager_notify.client import notify_pager
    from pager_notify.mission_page import format_mission_awareness_page
    from pager_notify.so_what import SMS_SUBJECT_MAX, clip

    subject = clip(f"Conductor hop parked — {reason}", SMS_SUBJECT_MAX)
    _subj, body, tag = format_mission_awareness_page(
        subject=subject,
        vision=(
            "ULG conductor missions should chain across G-rows without you "
            "babysitting each crash or loop."
        ),
        looking_back=(
            f"Mission {work_key} on worker thread {thread_id} hit hop budget "
            f"{reason} at dispatch {dispatch_id}."
        ),
        architecture=(
            "git_integration_worker conductor_hop reactor stamped "
            "PARKED_TRANSPORT on the worker thread and emitted "
            "frontier.sdk.conductor.hop.parked."
        ),
        looking_ahead=(
            "Harvest the summoning thread; resume only after fixing the "
            "underlying row or resetting budget state."
        ),
        beyond_bullets=[
            "Liaison must not second-fire the successor — park is substrate-owned.",
        ],
        tag="conductor-hop-parked",
    )
    try:
        return await notify_pager(_subj, body, tag=tag)
    except Exception:  # noqa: BLE001
        logger.warning(
            "hop budget park pager failed dispatch=%s thread=%s",
            dispatch_id,
            thread_id,
            exc_info=True,
        )
        return False


async def park_conductor_hop_mission(
    row: dict[str, Any],
    *,
    reason: str,
    poster: Any | None = None,
) -> None:
    """Park path: worker-thread line, event, awareness page, ledger stamp."""
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )
    from services.git_integration_worker.cursor_sdk_hop_events import (
        emit_frontier_sdk_conductor_hop_parked,
    )
    from services.git_integration_worker.cursor_sdk_ledger_hop import (
        hop_fields_from_record_json,
    )

    dispatch_id = str(row.get("dispatch_id") or "")
    thread_id = str(row.get("thread_id") or "")
    work_key = str(row.get("work_key") or "")
    hop_fields = hop_fields_from_record_json(str(row.get("record_json") or ""))
    hop_seq = hop_fields.get("hop_seq")
    hop_seq_int = int(hop_seq) if isinstance(hop_seq, int) else None

    body = build_parked_transport_body(reason=reason, hop_seq=hop_seq_int)
    if thread_id:
        try:
            (poster or default_park_poster)(thread_id, body)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "conductor hop park bus post failed dispatch=%s thread=%s err=%s",
                dispatch_id,
                thread_id,
                exc,
            )

    emit_frontier_sdk_conductor_hop_parked(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        hop_seq=hop_seq_int or 0,
        reason=reason,
    )

    ledger = CursorDispatchLedger.instance()
    ledger.merge_record_json(
        dispatch_id=dispatch_id,
        patch={
            HOP_PARKED_KEY: True,
            HOP_PARK_REASON_KEY: reason,
            **build_budget_authority_patch(row),
        },
    )

    await page_hop_budget_parked(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        reason=reason,
        work_key=work_key,
    )


__all__ = [
    "build_parked_transport_body",
    "default_park_poster",
    "page_hop_budget_parked",
    "park_conductor_hop_mission",
]

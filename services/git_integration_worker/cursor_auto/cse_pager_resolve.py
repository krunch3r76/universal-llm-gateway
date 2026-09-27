"""Live pager-id → current CSE URL lookup for cursor-auto completion paste.

Resolves the operator CSE delivery address at paste time via hop-watch, CSR,
registry scan, and job-stamp fallbacks without launching seats or mutating jobs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cdp_ask.operator_seat_resolve import (
    registration_resolvable_via_provenance,
    resolve_operator_seat,
)
from claude_bundles.cdp_registry.session_address import (
    chat_url_for_registration,
    list_active,
)
from claude_bundles.cdp_registry_store import load_sessions
from claude_bundles.cse_session_obligations import (
    resolve_payment_channel,
    stamp_session_ids,
)
from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.hop_cadence_home_lane import (
    watch_thread_for_job,
)
from services.git_integration_worker.cursor_auto.hop_cadence_watch import (
    load_watches,
    save_watches,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob

logger = get_logger(__name__)

__all__ = [
    "attempt_live_wake_followup",
    "build_wake_prompt_text",
    "live_identity_for_job",
    "map_followup_code",
    "owner_lane_for_delivery",
    "pager_key_for_job",
    "refresh_pager_after_hop",
    "refresh_pager_identity",
    "resolve_closeout_delivery_address",
    "resolve_live_cse_address",
]


def pager_key_for_job(job: AutoJob) -> str:
    """Return the hop-watch ledger key for an Auto job (home lane or thread id)."""
    return watch_thread_for_job(job)


def _registration_listable(registration_id: str | None) -> bool:
    rid = (registration_id or "").strip()
    if not rid:
        return False
    for reg in list_active():
        if reg.registration_id == rid:
            return True
    if (chat_url_for_registration(rid) or "").strip():
        return True
    return registration_resolvable_via_provenance(rid)


def _url_for_registration(registration_id: str | None) -> str | None:
    rid = (registration_id or "").strip()
    if not rid:
        return None
    return (chat_url_for_registration(rid) or "").strip() or None


def _empty_address() -> dict[str, str | None]:
    return {"chat_url": None, "registration_id": None, "source": ""}


def _from_holder_table(pager_key: str) -> dict[str, str | None]:
    from services.git_integration_worker.cse_session_holders import (
        get_driving_holder_for_lane,
    )
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    with CursorDispatchLedger.instance()._connect() as conn:
        row = get_driving_holder_for_lane(conn, pager_key)
    if row is None:
        return _empty_address()
    reg = str(row.get("registration_id") or "").strip() or None
    url = str(row.get("chat_url") or "").strip() or None
    if not url and not reg:
        return _empty_address()
    return {"chat_url": url, "registration_id": reg, "source": "cse_session_holders"}


def _from_hop_watch(pager_key: str) -> dict[str, str | None]:
    holder_first = _from_holder_table(pager_key)
    if _has_identity(holder_first):
        return holder_first
    row = load_watches().get(pager_key) or {}
    reg = str(row.get("registration_id") or "").strip()
    if not reg or not _registration_listable(reg):
        return _empty_address()
    url = _url_for_registration(reg) or str(row.get("chat_url") or "").strip() or None
    if not url and not reg:
        return _empty_address()
    return {"chat_url": url, "registration_id": reg or None, "source": "hop_watch"}


def _has_identity(result: dict[str, str | None]) -> bool:
    return bool(
        (result.get("chat_url") or "").strip()
        or (result.get("registration_id") or "").strip()
    )


def _from_csr(job: AutoJob, pager_key: str) -> dict[str, str | None]:
    sessions = load_sessions()
    for thread in (pager_key, str(job.thread_id)):
        channel = resolve_payment_channel(sessions, thread=thread)
        reg = (channel.get("registration_id") or "").strip() or None
        if reg and not _registration_listable(reg):
            continue
        url = (
            _url_for_registration(reg)
            or (channel.get("chat_url") or "").strip()
            or None
        )
        if url or reg:
            return {"chat_url": url, "registration_id": reg, "source": "csr"}
    return _empty_address()


def _from_registry(pager_key: str) -> dict[str, str | None]:
    resolved = resolve_operator_seat(pager_key)
    if resolved.get("source") == "unavailable":
        return {
            "chat_url": None,
            "registration_id": None,
            "source": "unavailable",
        }
    reg_id = (resolved.get("registration_id") or "").strip() or None
    if not reg_id:
        return _empty_address()
    url = (resolved.get("chat_url") or "").strip() or _url_for_registration(reg_id)
    return {"chat_url": url, "registration_id": reg_id, "source": "registry"}


def _from_job_stamp(job: AutoJob) -> dict[str, str | None]:
    reg = (getattr(job, "cse_registration_id", None) or "").strip() or None
    url = (getattr(job, "cse_chat_url", None) or "").strip() or None
    if reg and not _registration_listable(reg):
        reg = None
        url = None
    elif reg and not url:
        url = _url_for_registration(reg)
    if not url and not reg:
        return _empty_address()
    return {"chat_url": url, "registration_id": reg, "source": "job_stamp"}


def owner_lane_for_delivery(job: AutoJob) -> str:
    """Parent lane when the job thread is a child; otherwise the job thread.

    Closeout delivery targets the operator seat that owns the lane, which is
    seated on the parent, not on the child commission thread.
    """
    return _owner_lane_id(str(job.thread_id))


def _owner_lane_id(thread_id: str) -> str:
    tid = (thread_id or "").strip()
    if not tid:
        return tid
    try:
        from agent_bus_store.db.lane_associations import get_current_lane

        lane = get_current_lane(thread_id=tid)
    except Exception as exc:  # noqa: BLE001 — missing thread must not block delivery
        logger.debug("owner lane lookup failed thread=%s: %s", tid, exc)
        return tid
    parent = str(lane.get("parent_thread") or "").strip()
    return parent or tid


def _closeout_turn_number(thread_id: str) -> str:
    """Latest turn on the child lane after the closeout relay has been posted."""
    tid = (thread_id or "").strip()
    if not tid:
        return "unknown"
    try:
        from agent_bus_store.db.connection import connect

        with connect() as conn:
            row = conn.execute(
                "SELECT MAX(turn_number) AS n FROM turns WHERE thread = ?",
                (tid,),
            ).fetchone()
    except Exception as exc:  # noqa: BLE001
        logger.debug("closeout turn lookup failed thread=%s: %s", tid, exc)
        return "unknown"
    if row is None or row["n"] is None:
        return "unknown"
    return str(row["n"])


def resolve_closeout_delivery_address(job: AutoJob) -> dict[str, str | None]:
    """CSE address for a closeout followup, from seating identity only.

    Looks up the owner lane (parent of a child commission), then the child,
    then the home-lane pager key, on the hop watch, the driving holder, the
    CSR, and the operator-seat registry. Does not read ``job.cse_chat_url``
    or the thread CSE columns — those were the stale target behind the
    2026-09-27 0/2.
    """
    child = str(job.thread_id)
    owner = owner_lane_for_delivery(job)
    pager = pager_key_for_job(job)
    keys: list[str] = []
    for key in (owner, child, pager):
        cleaned = (key or "").strip()
        if cleaned and cleaned not in keys:
            keys.append(cleaned)
    for key in keys:
        for result in (
            _from_hop_watch(key),
            _from_csr(job, key),
            _from_registry(key),
        ):
            if result.get("source") == "unavailable":
                continue
            if _has_identity(result):
                logger.info(
                    "closeout_delivery_address job_thread=%s key=%s source=%s reg=%s",
                    child,
                    key,
                    result.get("source"),
                    result.get("registration_id"),
                )
                return result
    logger.info(
        "closeout_delivery_address no seating job_thread=%s owner=%s",
        child,
        owner,
    )
    return _empty_address()


def resolve_live_cse_address(job: AutoJob) -> dict[str, str | None]:
    """Resolve the current CSE delivery address for a pager key (never mutates *job*)."""
    pager_key = pager_key_for_job(job)
    for result in (
        _from_hop_watch(pager_key),
        _from_csr(job, pager_key),
        _from_registry(pager_key),
        _from_job_stamp(job),
    ):
        if _has_identity(result):
            logger.info(
                "cse_pager_resolve thread=%s source=%s reg=%s",
                pager_key,
                result.get("source"),
                result.get("registration_id"),
            )
            return result
    return _empty_address()


def live_identity_for_job(
    job: AutoJob,
    *,
    chat_url: str | None = None,
    registration_id: str | None = None,
    source: str | None = None,
) -> dict[str, str | None]:
    """Return an explicit address, or the closeout seating lookup.

    A passed side is used as-is. The missing side is not filled from
    ``job.cse_chat_url`` or ``job.cse_registration_id`` — that fill
    reattached a stale chat when seating returned only one side.
    """
    if chat_url is not None or registration_id is not None:
        return {
            "chat_url": (chat_url or "").strip() or None,
            "registration_id": (registration_id or "").strip() or None,
            "source": (source or "").strip(),
        }
    return resolve_closeout_delivery_address(job)


async def attempt_live_wake_followup(
    job: AutoJob,
    *,
    dispatch_id: str,
    request_turn: str,
    closeout_status: str,
    post: Any | None = None,
) -> tuple[bool, dict[str, Any], str | None]:
    """Try a chat followup using live identity; returns (ok, delivery, source)."""
    from claude_bundles.operator_mailbox import is_operator_proxy_mailbox

    from services.git_integration_worker.cursor_auto.cse_wake_delivery import (
        maybe_deliver_cse_wake,
    )

    if not is_operator_proxy_mailbox(job.from_agent):
        return False, {"ok": False, "skipped": True}, None
    live = resolve_closeout_delivery_address(job)
    source = live.get("source") or None
    if not _has_identity(live):
        logger.info(
            "closeout followup skipped no seating thread=%s owner=%s",
            job.thread_id,
            owner_lane_for_delivery(job),
        )
        return False, {"ok": False, "skipped": True, "reason": "no_identity"}, source
    delivery = await maybe_deliver_cse_wake(
        job,
        dispatch_id=dispatch_id,
        request_turn=request_turn,
        closeout_status=closeout_status,
        post=post,
        chat_url=live.get("chat_url"),
        registration_id=live.get("registration_id"),
        source=source,
    )
    return bool(delivery.get("ok")), delivery, source


def build_wake_prompt_text(
    *,
    dispatch_id: str,
    thread_id: str,
    request_turn: str,
    closeout_status: str,
) -> str:
    """Skill-reload followup body. ``closeout_status`` is not rendered (rank rots)."""
    del request_turn, closeout_status
    from services.git_integration_worker.cursor_auto.operator_wake_body import (
        render_operator_wake_body,
    )

    child = str(thread_id)
    return render_operator_wake_body(
        owner_lane=_owner_lane_id(child),
        child_lane=child,
        dispatch_id=dispatch_id,
        closeout_turn=_closeout_turn_number(child),
    )


def map_followup_code(result: dict[str, Any]) -> str:
    """Map followup delivery payload to a ``csr.wake.*`` payment status code."""
    if result.get("skipped"):
        reason = str(result.get("reason") or result.get("error") or "skipped")
        return (
            "csr.wake.followup_failed"
            if reason == "not_chat_delivery_capable"
            else "csr.wake.no_identity"
        )
    if result.get("error") == "no_identity":
        return "csr.wake.no_identity"
    if result.get("error") == "send_unverified" or result.get("send_verified") is False:
        return "csr.wake.send_unverified"
    return "csr.wake.unit_ok" if result.get("ok") else "csr.wake.followup_failed"


def refresh_pager_identity(
    thread: str,
    *,
    chat_url: str | None,
    registration_id: str | None,
    path: Path | None = None,
) -> None:
    """Persist successor chat_url/registration_id on the watch row without cadence fields."""
    tid = (thread or "").strip()
    chat = (chat_url or "").strip() or None
    reg = (registration_id or "").strip() or None
    if not tid or (not chat and not reg):
        return
    watches = load_watches(path)
    row = dict(watches.get(tid) or {"thread_id": tid})
    row["thread_id"] = tid
    if reg:
        row["registration_id"] = reg
    if chat:
        row["chat_url"] = chat
    watches[tid] = row
    save_watches(watches, path)
    stamp_session_ids(lane_thread=tid, chat_url=chat, registration_id=reg)


def refresh_pager_after_hop(
    thread: str,
    execution_id: str,
    *,
    path: Path | None = None,
) -> None:
    """Best-effort identity refresh after a continuity hop commissions a successor."""
    lane = (thread or "").strip()
    exec_id = (execution_id or "").strip()
    if not lane or not exec_id:
        return
    try:
        from services.git_integration_worker.cursor_auto.cdp_escalation import (
            read_cdp_lane_snapshot,
        )
        from services.git_integration_worker.cursor_auto.hop_cadence_predecessor import (
            op_row_for_execution_on_lane,
        )

        snap = read_cdp_lane_snapshot()
        if not isinstance(snap, dict):
            return
        aw_row = op_row_for_execution_on_lane(snap, lane, exec_id)
        if aw_row is None:
            return
        reg = str(aw_row.get("registration_id") or "").strip() or None
        chat = str(aw_row.get("chat_url") or "").strip() or None
        if not chat and reg:
            chat = _url_for_registration(reg)
        if not chat and not reg:
            return
        refresh_pager_identity(lane, chat_url=chat, registration_id=reg, path=path)
    except Exception as exc:  # noqa: BLE001 — hop must not fail on pager refresh
        logger.warning("refresh_pager_after_hop failed thread=%s: %s", lane, exc)

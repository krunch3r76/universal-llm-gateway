"""Live pager-id → current CSE URL lookup for cursor-auto completion paste.

Resolves the operator CSE delivery address at paste time via hop-watch, CSR,
registry scan, and job-stamp fallbacks without launching seats or mutating jobs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from claude_bundles.cdp_registry.session_address import (
    chat_url_for_registration,
    list_active,
)
from claude_bundles.cdp_registry_store import load_active as load_active_rows
from claude_bundles.cdp_registry_store import load_sessions
from claude_bundles.cse_session_obligations import (
    resolve_payment_channel,
    stamp_session_ids,
)
from claude_bundles.what_is_running_view import OPERATOR_PURPOSES
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
    "pager_key_for_job",
    "refresh_pager_after_hop",
    "refresh_pager_identity",
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
    return bool((chat_url_for_registration(rid) or "").strip())


def _url_for_registration(registration_id: str | None) -> str | None:
    rid = (registration_id or "").strip()
    if not rid:
        return None
    return (chat_url_for_registration(rid) or "").strip() or None


def _empty_address() -> dict[str, str | None]:
    return {"chat_url": None, "registration_id": None, "source": ""}


def _from_hop_watch(pager_key: str) -> dict[str, str | None]:
    row = load_watches().get(pager_key) or {}
    reg = str(row.get("registration_id") or "").strip()
    if not reg or not _registration_listable(reg):
        return _empty_address()
    url = _url_for_registration(reg) or str(row.get("chat_url") or "").strip() or None
    if not url and not reg:
        return _empty_address()
    return {"chat_url": url, "registration_id": reg or None, "source": "hop_watch"}


def _has_identity(result: dict[str, str | None]) -> bool:
    return bool((result.get("chat_url") or "").strip() or (result.get("registration_id") or "").strip())


def _from_csr(job: AutoJob, pager_key: str) -> dict[str, str | None]:
    sessions = load_sessions()
    for thread in (pager_key, str(job.thread_id)):
        channel = resolve_payment_channel(sessions, thread=thread)
        reg = (channel.get("registration_id") or "").strip() or None
        if reg and not _registration_listable(reg):
            continue
        url = _url_for_registration(reg) or (channel.get("chat_url") or "").strip() or None
        if url or reg:
            return {"chat_url": url, "registration_id": reg, "source": "csr"}
    return _empty_address()


def _from_registry(pager_key: str) -> dict[str, str | None]:
    active = load_active_rows()
    rows: list[tuple[str, str | None, float]] = []
    for reg in list_active():
        purpose = (reg.purpose or "").strip()
        if purpose not in OPERATOR_PURPOSES:
            continue
        if str(reg.parent_thread or "").strip() != pager_key:
            continue
        raw = active.get(reg.registration_id) or {}
        started = raw.get("started_at")
        try:
            started_f = float(started) if started is not None else 0.0
        except (TypeError, ValueError):
            started_f = 0.0
        kind = str(reg.mission_kind or "root").strip().lower() or "root"
        rows.append((reg.registration_id, kind, started_f))
    if not rows:
        return _empty_address()
    hop_rows = [r for r in rows if r[1] == "hop"]
    if len(hop_rows) == 1:
        reg_id = hop_rows[0][0]
    else:
        reg_id = max(rows, key=lambda r: r[2])[0]
    url = _url_for_registration(reg_id)
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
) -> dict[str, str | None]:
    """Return delivery address from explicit overrides or the live lookup ladder."""
    if chat_url is not None or registration_id is not None:
        return {
            "chat_url": chat_url or getattr(job, "cse_chat_url", None),
            "registration_id": registration_id or getattr(job, "cse_registration_id", None),
            "source": "",
        }
    return resolve_live_cse_address(job)


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
    live = resolve_live_cse_address(job)
    source = live.get("source") or None
    if not _has_identity(live):
        return False, {"ok": False, "skipped": True}, source
    delivery = await maybe_deliver_cse_wake(
        job,
        dispatch_id=dispatch_id,
        request_turn=request_turn,
        closeout_status=closeout_status,
        post=post,
        chat_url=live.get("chat_url"),
        registration_id=live.get("registration_id"),
    )
    return bool(delivery.get("ok")), delivery, source


def build_wake_prompt_text(
    *,
    dispatch_id: str,
    thread_id: str,
    request_turn: str,
    closeout_status: str,
) -> str:
    """Token-free wake body for in-chat delivery (not a CLOSEOUT envelope copy)."""
    return (
        "Park-on-WAKE delivery (leg b).\n"
        f"dispatch_id: {dispatch_id}\n"
        f"thread: {thread_id}\n"
        f"request_turn: {request_turn}\n"
        f"closeout_status: {closeout_status}\n"
        "\n"
        "Harvest: mark_read → wait(wait_seconds=0) → validate dispatch_id vs lane tip."
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

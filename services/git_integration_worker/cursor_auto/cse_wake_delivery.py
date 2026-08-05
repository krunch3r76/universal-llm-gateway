"""Post-CLOSEOUT in-chat delivery to a retained CSE (park-on-WAKE leg b).

Thin HTTP caller to the existing ``/v1/project-ask/followups`` endpoint.
Identity ladder and reattach remain owned by ``cdp_ask`` — no second probe.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Protocol

import httpx
from agent_seat.registry import normalize_bus_address
from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.queue import AutoJob

logger = get_logger(__name__)

_DEFAULT_TIMEOUT_S = 60.0
_FOLLOWUPS_PATH = "/v1/project-ask/followups"


class HttpPoster(Protocol):
    def __call__(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        timeout: float,
    ) -> httpx.Response: ...


def is_chat_delivery_capable(from_agent: str) -> bool:
    """Cowork / web-anthropic-class callers; false for IDE ``cursor``."""
    if not from_agent or not str(from_agent).strip():
        return False
    addr = normalize_bus_address(str(from_agent).strip())
    if addr == "cursor":
        return False
    return addr.startswith("web-")


def _project_ask_url() -> str:
    return os.environ.get("PROJECT_ASK_URL", "").strip()


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


def deliver_cse_wake(
    *,
    chat_url: str | None,
    registration_id: str | None,
    prompt_text: str,
    purpose: str = "operator-proxy",
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    post: HttpPoster | None = None,
) -> dict[str, Any]:
    """POST followups once; non-ok is a single dead-CSE degrade signal (I7)."""
    chat = (chat_url or "").strip()
    registration = (registration_id or "").strip()
    if not chat and not registration:
        return {"ok": False, "error": "no_identity", "skipped": True}

    if not (prompt_text or "").strip():
        return {"ok": False, "error": "no_prompt", "skipped": True}

    base = _project_ask_url()
    if not base:
        return {
            "ok": False,
            "error": "project_ask_unconfigured",
            "skipped": True,
        }

    body: dict[str, Any] = {
        "prompt_text": prompt_text.strip(),
        "purpose": purpose,
        "timeout_s": int(timeout_s),
    }
    if chat:
        body["chat_url"] = chat
        body["reattach"] = True
    if registration:
        body["registration_id"] = registration

    url = f"{base.rstrip('/')}{_FOLLOWUPS_PATH}"
    try:
        if post is not None:
            resp = post("POST", url, json=body, timeout=timeout_s)
        else:
            with httpx.Client(timeout=timeout_s) as client:
                resp = client.post(url, json=body)
        if resp.status_code >= 400:
            return {
                "ok": False,
                "error": f"project-ask HTTP {resp.status_code}",
                "status_code": resp.status_code,
                "detail": (resp.text or "")[:400],
            }
        data = resp.json() if resp.content else {"ok": True}
        if isinstance(data, dict) and data.get("ok") is False:
            return {**data, "ok": False}
        if isinstance(data, dict):
            return {"ok": True, **data}
        return {"ok": True}
    except httpx.HTTPError as exc:
        logger.warning("cse_wake_delivery unreachable url=%s error=%s", url, exc)
        return {"ok": False, "error": f"project-ask unreachable: {exc}"}


async def maybe_deliver_cse_wake(
    job: AutoJob,
    *,
    dispatch_id: str,
    request_turn: str,
    closeout_status: str,
    post: HttpPoster | None = None,
    chat_url: str | None = None,
    registration_id: str | None = None,
) -> dict[str, Any]:
    """Leg (b): fire followup after bus WAKE; skip IDE-class or missing identity."""
    if not is_chat_delivery_capable(job.from_agent):
        return {"ok": False, "skipped": True, "reason": "not_chat_delivery_capable"}

    chat_url = chat_url or getattr(job, "cse_chat_url", None)
    registration_id = registration_id or getattr(job, "cse_registration_id", None)
    prompt = build_wake_prompt_text(
        dispatch_id=dispatch_id,
        thread_id=str(job.thread_id),
        request_turn=request_turn,
        closeout_status=closeout_status,
    )
    return await asyncio.to_thread(
        deliver_cse_wake,
        chat_url=chat_url,
        registration_id=registration_id,
        prompt_text=prompt,
        post=post,
    )


def _map_followup_code(result: dict[str, Any]) -> str:
    if result.get("skipped"):
        reason = str(result.get("reason") or result.get("error") or "skipped")
        if reason == "not_chat_delivery_capable":
            return "csr.wake.followup_failed"
        return "csr.wake.no_identity"
    if result.get("error") == "no_identity":
        return "csr.wake.no_identity"
    if result.get("error") == "send_unverified" or result.get("send_verified") is False:
        return "csr.wake.send_unverified"
    if not result.get("ok"):
        return "csr.wake.followup_failed"
    return "csr.wake.unit_ok"


async def pay_wake_unit(
    job: AutoJob,
    *,
    dispatch_id: str,
    request_turn: str,
    closeout_status: str,
    bus: Any | None = None,
    post: HttpPoster | None = None,
) -> dict[str, Any]:
    """Transactional pay unit — bus WAKE is mandatory; chat followup is debt-backed."""
    from claude_bundles.cdp_registry_store import load_sessions
    from claude_bundles.cse_session_obligations import (
        get_open_wake_owed,
        record_wake_posted,
        resolve_payment_channel,
    )
    from claude_bundles.cse_wake_retain import (
        release_lane_if_debt_cleared,
        try_claim_wake_payment,
    )

    from services.git_integration_worker.cursor_auto.nested_sdk import (
        post_operator_wake,
    )

    thread = str(job.thread_id)
    sessions = load_sessions()
    ob = get_open_wake_owed(sessions, thread=thread)
    if ob is None:
        wake = await post_operator_wake(
            job,
            dispatch_id=dispatch_id,
            request_turn=request_turn,
            closeout_status=closeout_status,
            bus=bus,
        )
        wake_ok = bool(wake.get("ok"))
        return {
            "ok": wake_ok,
            "skipped": False,
            "code": "csr.wake.no_debt_bus_wake",
            "followup_ok": False,
            "wake_ok": wake_ok,
            "wake": wake,
        }

    obligation_id = str(ob.get("obligation_id") or "")
    wake_channel = str(ob.get("wake_channel") or "chat_delivery")
    payment = ob.get("payment") or {}
    if payment.get("followup_ok") or ob.get("status") == "discharged":
        return {
            "ok": True,
            "skipped": True,
            "code": "csr.wake.already_paid",
            "followup_ok": True,
            "wake_ok": False,
        }

    channel = resolve_payment_channel(sessions, thread=thread)
    chat_url = channel.get("chat_url")
    registration_id = channel.get("registration_id")
    if not chat_url and not registration_id:
        chat_url = getattr(job, "cse_chat_url", None)
        registration_id = getattr(job, "cse_registration_id", None)

    followup_ok = False
    followup_code = "csr.wake.skipped"
    delivery: dict[str, Any] = {"ok": False, "skipped": True}

    if wake_channel == "chat_delivery":
        if not try_claim_wake_payment(thread=thread, obligation_id=obligation_id):
            if payment.get("claimed"):
                return {
                    "ok": True,
                    "skipped": True,
                    "code": "csr.wake.claim_inflight",
                    "followup_ok": False,
                    "wake_ok": False,
                }
        delivery = await maybe_deliver_cse_wake(
            job,
            dispatch_id=dispatch_id,
            request_turn=request_turn,
            closeout_status=closeout_status,
            post=post,
            chat_url=chat_url,
            registration_id=registration_id,
        )
        followup_ok = bool(delivery.get("ok"))
        followup_code = _map_followup_code(delivery)
        if followup_ok and registration_id:
            release_lane_if_debt_cleared(
                str(registration_id), purpose="operator-proxy"
            )

    wake: dict[str, Any] = {"ok": False, "skipped": True}
    wake_ok = False
    if not followup_ok and wake_channel == "chat_delivery":
        wake = await post_operator_wake(
            job,
            dispatch_id=dispatch_id,
            request_turn=request_turn,
            closeout_status=closeout_status,
            bus=bus,
        )
        wake_ok = bool(wake.get("ok"))
        ob = get_open_wake_owed(load_sessions(), thread=thread)
        if wake_ok and ob:
            record_wake_posted(
                thread=thread,
                obligation_id=str(ob.get("obligation_id") or obligation_id),
            )
        try:
            from pager_notify.client import notify_pager

            await notify_pager(
                f"WAKE followup failed thread {thread}",
                f"dispatch={dispatch_id} code={followup_code}",
                tag="wake-degrade",
            )
        except Exception:
            logger.warning("wake degrade pager failed thread=%s", thread)

    if followup_ok:
        code = "csr.wake.unit_ok"
    elif wake_channel != "chat_delivery":
        code = "csr.wake.channel_skipped"
    elif not followup_ok and wake_ok:
        code = "csr.wake.degraded_bus_wake"
    elif not followup_ok:
        code = followup_code if followup_code != "csr.wake.unit_ok" else "csr.wake.followup_failed"
    else:
        code = "csr.wake.bus_wake_failed"

    ok = followup_ok or (wake_channel != "chat_delivery")
    return {
        "ok": ok,
        "followup_ok": followup_ok,
        "wake_ok": wake_ok,
        "code": code,
        "delivery": delivery,
        "wake": wake,
    }


__all__ = [
    "build_wake_prompt_text",
    "deliver_cse_wake",
    "is_chat_delivery_capable",
    "maybe_deliver_cse_wake",
    "pay_wake_unit",
]

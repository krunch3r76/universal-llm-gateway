"""Post-CLOSEOUT in-chat delivery to a retained CSE (park-on-WAKE leg b).

Thin HTTP caller to the existing ``/v1/project-ask/followups`` endpoint.
Identity ladder and reattach remain owned by ``cdp_ask`` — no second probe.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Protocol

import httpx
from cdp_ask.client import format_cdp_ask_http_error
from claude_bundles.operator_mailbox import is_operator_proxy_mailbox
from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.queue import AutoJob

logger = get_logger(__name__)

_DEFAULT_TIMEOUT_S = 60.0
# Same class as MCP cse_session_warm.HTTP_TIMEOUT_SLACK_S — equal httpx vs
# satellite budgets report unreachable while paste_verified still fires.
_HTTP_TIMEOUT_SLACK_S = 60.0
_FOLLOWUPS_PATH = "/v1/project-ask/followups"


class HttpPoster(Protocol):
    """Test-injected callable that POSTs JSON to project-ask followups."""

    def __call__(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        timeout: float,
    ) -> httpx.Response: ...


def is_chat_delivery_capable(from_agent: str) -> bool:
    """Return True for Cowork operator-proxy mailboxes; IDE ``cursor`` is excluded."""
    return is_operator_proxy_mailbox(from_agent)


def _project_ask_url() -> str:
    return os.environ.get("PROJECT_ASK_URL", "").strip()


def deliver_cse_wake(
    *,
    chat_url: str | None,
    registration_id: str | None,
    prompt_text: str,
    purpose: str = "operator-proxy",
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    post: HttpPoster | None = None,
    reattach: bool = True,
    retain_lane: bool = False,
) -> dict[str, Any]:
    """POST followups once; non-ok is a single dead-CSE degrade signal (I7).

    Park-on-WAKE defaults ``reattach=True`` (dormant seats). Wait-report uses
    ``reattach=False`` and ``retain_lane=True`` so teardown does not park.
    """
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
        if reattach:
            body["reattach"] = True
    if retain_lane:
        body["retain_lane"] = True
    if registration:
        body["registration_id"] = registration

    url = f"{base.rstrip('/')}{_FOLLOWUPS_PATH}"
    http_timeout = float(timeout_s) + _HTTP_TIMEOUT_SLACK_S
    try:
        if post is not None:
            resp = post("POST", url, json=body, timeout=http_timeout)
        else:
            with httpx.Client(timeout=http_timeout) as client:
                resp = client.post(url, json=body)
        if resp.status_code >= 400:
            detail = (resp.text or "")[:400]
            return {
                "ok": False,
                "error": format_cdp_ask_http_error(resp.status_code, detail),
                "status_code": resp.status_code,
                "detail": detail,
            }
        data = resp.json() if resp.content else {"ok": True}
        if isinstance(data, dict) and data.get("ok") is False:
            return {**data, "ok": False}
        if isinstance(data, dict):
            return {"ok": True, **data}
        return {"ok": True}
    except httpx.TimeoutException as exc:
        logger.warning("cse_wake_delivery timeout url=%s error=%s", url, exc)
        return {
            "ok": False,
            "code": "cse_session_http_timeout",
            "error": f"cdp-ask timed out after {http_timeout:.0f}s waiting for satellite",
            "retryable": True,
            "indeterminate": True,
        }
    except httpx.HTTPError as exc:
        logger.warning("cse_wake_delivery unreachable url=%s error=%s", url, exc)
        return {"ok": False, "error": f"cdp-ask unreachable: {exc}"}


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
    from services.git_integration_worker.cursor_auto.cse_pager_resolve import (
        build_wake_prompt_text,
        live_identity_for_job,
    )

    if not is_chat_delivery_capable(job.from_agent):
        return {"ok": False, "skipped": True, "reason": "not_chat_delivery_capable"}

    live = live_identity_for_job(job, chat_url=chat_url, registration_id=registration_id)
    chat_url = live.get("chat_url")
    registration_id = live.get("registration_id")
    source = live.get("source") or None
    prompt = build_wake_prompt_text(
        dispatch_id=dispatch_id,
        thread_id=str(job.thread_id),
        request_turn=request_turn,
        closeout_status=closeout_status,
    )
    result = await asyncio.to_thread(
        deliver_cse_wake,
        chat_url=chat_url,
        registration_id=registration_id,
        prompt_text=prompt,
        post=post,
    )
    if source:
        result = {**result, "source": source}
    return result


async def pay_wake_unit(
    job: AutoJob,
    *,
    dispatch_id: str,
    request_turn: str,
    closeout_status: str,
    bus: Any | None = None,
    post: HttpPoster | None = None,
) -> dict[str, Any]:
    """Transactional pay unit — live followup first; bus WAKE is mandatory fallback."""
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

    from services.git_integration_worker.cursor_auto.cse_pager_resolve import (
        attempt_live_wake_followup,
        map_followup_code,
        resolve_live_cse_address,
    )
    from services.git_integration_worker.cursor_auto.nested_sdk import (
        post_operator_wake,
    )

    thread = str(job.thread_id)
    sessions = load_sessions()
    ob = get_open_wake_owed(sessions, thread=thread)
    if ob is None:
        followup_ok, delivery, source = await attempt_live_wake_followup(
            job,
            dispatch_id=dispatch_id,
            request_turn=request_turn,
            closeout_status=closeout_status,
            post=post,
        )
        if followup_ok:
            return {
                "ok": True,
                "skipped": False,
                "code": "csr.wake.unit_ok",
                "followup_ok": True,
                "wake_ok": False,
                "delivery": delivery,
                "wake": {"ok": False, "skipped": True},
                "source": source,
            }
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
            "delivery": delivery,
            "source": source,
        }

    payment = ob.get("payment") or {}
    if payment.get("followup_ok") or ob.get("status") == "discharged":
        return {
            "ok": True,
            "skipped": True,
            "code": "csr.wake.already_paid",
            "followup_ok": True,
            "wake_ok": False,
        }

    obligation_id = str(ob.get("obligation_id") or "")
    wake_channel = str(ob.get("wake_channel") or "chat_delivery")
    channel = resolve_payment_channel(sessions, thread=thread)
    followup_ok = False
    followup_code = "csr.wake.skipped"
    delivery: dict[str, Any] = {"ok": False, "skipped": True}
    source: str | None = None
    registration_id: str | None = None
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
        )
        followup_ok = bool(delivery.get("ok"))
        followup_code = map_followup_code(delivery)
        source = delivery.get("source")
        registration_id = channel.get("registration_id") or (
            resolve_live_cse_address(job).get("registration_id") if followup_ok else None
        )
        if followup_ok and registration_id:
            release_lane_if_debt_cleared(str(registration_id), purpose="operator-proxy")

    wake, wake_ok = {"ok": False, "skipped": True}, False
    if not followup_ok and wake_channel == "chat_delivery":
        wake = await post_operator_wake(
            job,
            dispatch_id=dispatch_id,
            request_turn=request_turn,
            closeout_status=closeout_status,
            bus=bus,
        )
        wake_ok = bool(wake.get("ok"))
        ob_live = get_open_wake_owed(load_sessions(), thread=thread)
        if wake_ok and ob_live:
            record_wake_posted(
                thread=thread,
                obligation_id=str(ob_live.get("obligation_id") or obligation_id),
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
    elif wake_ok:
        code = "csr.wake.degraded_bus_wake"
    elif not followup_ok:
        code = (
            followup_code
            if followup_code != "csr.wake.unit_ok"
            else "csr.wake.followup_failed"
        )
    else:
        code = "csr.wake.bus_wake_failed"
    return {
        "ok": followup_ok or wake_channel != "chat_delivery",
        "followup_ok": followup_ok,
        "wake_ok": wake_ok,
        "code": code,
        "delivery": delivery,
        "wake": wake,
        "source": source,
    }


__all__ = [
    "deliver_cse_wake",
    "is_chat_delivery_capable",
    "maybe_deliver_cse_wake",
    "pay_wake_unit",
]

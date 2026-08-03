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
) -> dict[str, Any]:
    """Leg (b): fire followup after bus WAKE; skip IDE-class or missing identity."""
    if not is_chat_delivery_capable(job.from_agent):
        return {"ok": False, "skipped": True, "reason": "not_chat_delivery_capable"}

    chat_url = getattr(job, "cse_chat_url", None)
    registration_id = getattr(job, "cse_registration_id", None)
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


__all__ = [
    "build_wake_prompt_text",
    "deliver_cse_wake",
    "is_chat_delivery_capable",
    "maybe_deliver_cse_wake",
]

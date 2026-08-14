"""Cowork leg: retained project-ask submit, poll, followup paste, harvest wait."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx
from cdp_ask.client import CdpAskClient
from cdp_ask.followup_resolve import normalize_cse_url
from claude_bundles import cdp_registry
from claude_bundles.chat_reply_wait import harvest_assistant, wait_assistant_reply
from claude_bundles.skills_ui_panel import connect_cdp

DEFAULT_PROJECT_ASK_URL = "http://127.0.0.1:8770"
_ADVANCE_PHASES = frozenset({"content_proof", "archiving", "terminal"})


@dataclass
class ClaudeSession:
    execution_id: str
    registration_id: str | None
    chat_url: str | None
    cdp_url: str | None
    baseline_body: str


class ClaudeLegError(RuntimeError):
    """project-ask submit/poll/followup failed."""


def _client(base_url: str) -> CdpAskClient:
    return CdpAskClient(base_url=base_url, timeout_s=60.0)


def submit_retained(
    *,
    prompt_text: str,
    base_url: str = DEFAULT_PROJECT_ASK_URL,
    holder: str = "web-chat-relay",
) -> dict[str, Any]:
    """Admit a converse+retain Cowork ask. Admission ≠ first reply."""
    return _client(base_url).submit(
        {
            "prompt_text": prompt_text,
            "holder": holder,
            "purpose": "ask",
            "model": "opus-5",
            "converse": True,
            "no_project_uuid": True,
            "ensure_cowork_auto": True,
            "delete_after": False,
            "expected_size": "small",
            "harvest_source": "chat",
            "timeout_s": 360,
        }
    )


def poll_execution(execution_id: str, *, base_url: str) -> dict[str, Any]:
    return _client(base_url).poll(execution_id)


def poll_until_harvestable(
    execution_id: str,
    *,
    base_url: str = DEFAULT_PROJECT_ASK_URL,
    timeout_s: float = 420.0,
    poll_s: float = 5.0,
) -> dict[str, Any]:
    """Wait until content_proof/terminal (not turn_idle alone) or failed."""
    deadline = time.time() + timeout_s
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = poll_execution(execution_id, base_url=base_url)
        phase = str(last.get("completion_phase") or "")
        status = str(last.get("status") or "")
        if status == "failed" or phase == "failed":
            raise ClaudeLegError(
                f"project-ask failed execution_id={execution_id} "
                f"phase={phase} error={last.get('error')!r}"
            )
        if phase in _ADVANCE_PHASES or last.get("archive_uri") or last.get("body"):
            if phase == "turn_idle" and not last.get("body") and not last.get(
                "content_proof_uri"
            ):
                time.sleep(poll_s)
                continue
            if phase != "turn_idle" or last.get("body"):
                return last
        time.sleep(poll_s)
    raise ClaudeLegError(
        f"project-ask poll timeout execution_id={execution_id} last={last!r}"
    )


def resolve_lane(
    *,
    registration_id: str | None,
    chat_url: str | None,
) -> tuple[str | None, str | None]:
    """Return ``(cdp_url, chat_url)`` from the Jupiter registry when possible."""
    bound_chat = chat_url
    if registration_id:
        bound_chat = bound_chat or cdp_registry.chat_url_for_registration(registration_id)
        for row in cdp_registry.list_active():
            if row.registration_id == registration_id:
                return row.cdp_url, bound_chat
    if bound_chat:
        target = normalize_cse_url(bound_chat)
        for row in cdp_registry.list_active():
            row_chat = cdp_registry.chat_url_for_registration(row.registration_id)
            if row_chat and normalize_cse_url(row_chat) == target:
                return row.cdp_url, bound_chat
    return None, bound_chat


def followup_paste(
    *,
    prompt_text: str,
    chat_url: str,
    base_url: str = DEFAULT_PROJECT_ASK_URL,
    registration_id: str | None = None,
    execution_id: str | None = None,
    timeout_s: int = 60,
) -> dict[str, Any]:
    """POST warm followup. Does not harvest the reply."""
    body = {
        "chat_url": chat_url,
        "prompt_text": prompt_text,
        "timeout_s": timeout_s,
        "min_receipt": "dom_paste",
    }
    if registration_id:
        body["registration_id"] = registration_id
    if execution_id:
        body["execution_id"] = execution_id
    try:
        with httpx.Client(base_url=base_url, timeout=timeout_s + 15) as http:
            resp = http.post("/v1/project-ask/followups", json=body)
    except httpx.RequestError as exc:
        raise ClaudeLegError(f"followup unreachable: {exc}") from exc
    if resp.status_code >= 400:
        raise ClaudeLegError(f"followup HTTP {resp.status_code}: {resp.text[:400]}")
    return resp.json() if resp.content else {"ok": True}


async def wait_next_assistant(
    *,
    cdp_url: str,
    chat_url: str,
    before: dict[str, Any] | None = None,
    timeout_s: int = 360,
) -> dict[str, Any]:
    """Harvest the next Cowork assistant turn on the attached lane."""
    pw, _browser, ctx, _page0 = await connect_cdp(cdp_url)
    target = normalize_cse_url(chat_url)
    try:
        page = None
        for candidate in ctx.pages:
            if normalize_cse_url(candidate.url or "") == target:
                page = candidate
                break
        if page is None:
            raise ClaudeLegError(f"CSE page not on lane cdp={cdp_url} chat={chat_url}")
        prior = before if before is not None else await harvest_assistant(page)
        return await wait_assistant_reply(page, before=prior, timeout_s=timeout_s)
    finally:
        await pw.stop()


def open_retained_session(
    *,
    grok_url: str,
    base_url: str = DEFAULT_PROJECT_ASK_URL,
) -> ClaudeSession:
    """Submit opener, poll to harvestable, resolve lane identity."""
    opener = (
        f"This CSE is relayed to {grok_url} — wait for the next Grok turn. "
        "Do not start a task until a Grok message is pasted."
    )
    admitted = submit_retained(prompt_text=opener, base_url=base_url)
    execution_id = str(admitted.get("execution_id") or "")
    if not execution_id:
        raise ClaudeLegError(f"submit missing execution_id: {admitted!r}")
    polled = poll_until_harvestable(execution_id, base_url=base_url)
    chat_url = polled.get("url") or None
    registration_id = polled.get("registration_id") or admitted.get("registration_id")
    cdp_url, chat_url = resolve_lane(
        registration_id=registration_id, chat_url=chat_url
    )
    return ClaudeSession(
        execution_id=execution_id,
        registration_id=registration_id,
        chat_url=chat_url,
        cdp_url=cdp_url,
        baseline_body=str(polled.get("body") or ""),
    )

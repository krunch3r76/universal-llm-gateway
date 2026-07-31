"""Scan operator-proxy bus threads and page on DISPOSITION / CLOSEOUT turns."""

from __future__ import annotations

import logging
import os

from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client

from pager_notify.client import notify_pager
from pager_notify.so_what import SMS_BODY_MAX, SMS_SUBJECT_MAX, clip
from pager_notify.state import load_last_turns, save_last_turn

logger = logging.getLogger(__name__)

_TIMEOUT_S = 15.0
_OPERATOR_PROXY_TAG = "operator-proxy"
_WATCH_THREADS_ENV = "PAGER_WATCH_THREADS"


def _auth_headers() -> dict[str, str]:
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _bus_scan_enabled() -> bool:
    raw = os.environ.get("PAGER_NOTIFY_BUS", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _watch_thread_ids() -> list[str]:
    explicit = os.environ.get(_WATCH_THREADS_ENV, "").strip()
    if explicit:
        return [part.strip() for part in explicit.split(",") if part.strip()]
    return []


def _turn_is_pageable(turn: dict) -> bool:
    subject = str(turn.get("subject") or "")
    body = str(turn.get("body") or "")
    sender = str(turn.get("from") or "")
    if sender == "web-anthropic" and (
        subject.startswith("DISPOSITION")
        or "TYPE: DISPOSITION" in body
        or subject.startswith("OPERATOR")
    ):
        return True
    if sender == "cursor-auto" and subject.startswith("status:done"):
        return True
    return False


def _summarize_turn(turn: dict, *, thread_summary: str = "") -> tuple[str, str]:
    thread = str(turn.get("thread") or "?")
    turn_no = turn.get("turn_number", "?")
    subject = str(turn.get("subject") or "")[:80]
    sender = str(turn.get("from") or "")
    so_what = (thread_summary or "").strip()
    if so_what:
        subj = clip(f"{so_what} · bus {thread} t{turn_no}", SMS_SUBJECT_MAX)
        body = clip(f"{sender}: {subject}", SMS_BODY_MAX)
    else:
        subj = f"bus {thread} t{turn_no}"
        body = f"{sender}: {subject}"
    return subj, body


async def _list_operator_threads(client) -> list[dict]:
    explicit = _watch_thread_ids()
    if explicit:
        return [{"id": tid} for tid in explicit]
    resp = await client.get(
        "/threads",
        params=[("status", "active"), ("tags", _OPERATOR_PROXY_TAG)],
        headers=_auth_headers(),
    )
    resp.raise_for_status()
    return list(resp.json().get("threads", []))


async def scan_operator_bus_turns() -> int:
    """Notify on new pageable turns; returns count of pages sent."""
    if not _bus_scan_enabled():
        return 0
    sent = 0
    last = load_last_turns()
    try:
        async with make_async_client(
            DEFAULT_AGENT_BUS_URL, timeout=_TIMEOUT_S
        ) as client:
            threads = await _list_operator_threads(client)
            for thread in threads:
                thread_id = str(thread.get("id") or "")
                if not thread_id:
                    continue
                thread_summary = str(thread.get("summary") or "")
                if not thread_summary:
                    try:
                        detail_resp = await client.get(
                            f"/threads/{thread_id}",
                            headers=_auth_headers(),
                        )
                        if detail_resp.status_code == 200:
                            thread_summary = str(
                                detail_resp.json().get("summary") or ""
                            )
                    except Exception:
                        thread_summary = ""
                resp = await client.get(
                    "/turns",
                    params={"thread": thread_id, "last": 20, "compact": "false"},
                    headers=_auth_headers(),
                )
                resp.raise_for_status()
                turns = list(resp.json().get("turns", []))
                cursor = last.get(thread_id, 0)
                if thread_id not in last and turns:
                    bootstrap = max(int(t.get("id") or 0) for t in turns)
                    save_last_turn(thread_id, bootstrap)
                    continue
                for turn in sorted(
                    turns, key=lambda t: int(t.get("id") or 0)
                ):
                    turn_id = int(turn.get("id") or 0)
                    if turn_id <= cursor:
                        continue
                    if not _turn_is_pageable(turn):
                        save_last_turn(thread_id, turn_id)
                        continue
                    subject, body = _summarize_turn(
                        turn, thread_summary=thread_summary
                    )
                    if await notify_pager(subject, body, tag="bus"):
                        sent += 1
                    save_last_turn(thread_id, turn_id)
    except Exception:
        logger.exception("pager bus scan failed")
    return sent

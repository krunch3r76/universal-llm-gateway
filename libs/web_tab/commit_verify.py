"""DOM commit verification — baseline, poll, readback (shared with paste_as_operator contract)."""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from web_tab.provider_selectors import (
    ProviderSite,
    baseline_snapshot_js,
    read_reply_js,
    verify_committed_js,
)


class DomEvaluate(Protocol):
    async def evaluate(
        self, *, ws_url: str, expression: str, await_promise: bool = False
    ) -> dict[str, Any]: ...


def flatten_paste_text(text: str) -> str:
    """Match paste_as_operator: one line for TipTap / send-button equality checks."""
    return " ".join((text or "").splitlines()).strip()


async def capture_dom_baseline(
    port: DomEvaluate,
    *,
    ws_url: str,
    site: ProviderSite,
) -> dict[str, Any]:
    raw = await port.evaluate(ws_url=ws_url, expression=baseline_snapshot_js(site=site))
    return raw if isinstance(raw, dict) else {}


async def poll_verify_committed(
    port: DomEvaluate,
    *,
    ws_url: str,
    site: ProviderSite,
    prompt_text: str,
    baseline_user_count: int,
    timeout_s: float = 8.0,
    poll_s: float = 0.25,
) -> dict[str, Any]:
    """Poll until latest user turn matches *prompt_text* and composer is empty."""
    flat = flatten_paste_text(prompt_text)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    last: dict[str, Any] = {}
    while loop.time() <= deadline:
        last = await port.evaluate(
            ws_url=ws_url,
            expression=verify_committed_js(
                site=site,
                prompt_text=flat,
                baseline_user_count=baseline_user_count,
            ),
        )
        if not isinstance(last, dict):
            last = {}
        if last.get("receipt") == "dom_committed":
            return {**last, "ok": True}
        await asyncio.sleep(poll_s)
    receipt = str(last.get("receipt") or "dom_paste")
    committed = receipt == "dom_committed"
    return {
        **last,
        "ok": committed,
        "receipt": receipt,
        "screenshot_fallback": not committed and not last.get("has_user_turn"),
    }


async def poll_read_reply(
    port: DomEvaluate,
    *,
    ws_url: str,
    site: ProviderSite,
    baseline_assistant_count: int,
    baseline_assistant_text: str,
    timeout_s: float = 45.0,
    poll_s: float = 0.75,
) -> dict[str, Any]:
    """Wait for a new assistant turn after *baseline_* snapshot."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    last: dict[str, Any] = {}
    base_text = (baseline_assistant_text or "").strip()
    stable_text: str | None = None
    while loop.time() <= deadline:
        last = await port.evaluate(
            ws_url=ws_url,
            expression=read_reply_js(
                site=site,
                baseline_assistant_count=baseline_assistant_count,
                baseline_assistant_text=base_text,
            ),
        )
        if not isinstance(last, dict):
            last = {}
        if last.get("complete"):
            text = str(last.get("text") or "").strip()
            if text and text == stable_text:
                return {
                    "ok": True,
                    "text": text,
                    "streaming": False,
                }
            stable_text = text if text else None
        else:
            stable_text = None
        await asyncio.sleep(poll_s)
    return {
        "ok": False,
        "code": "timeout",
        "reason": "assistant reply did not complete",
        "partial_text": str(last.get("text") or ""),
        "streaming": bool(last.get("streaming")),
    }


__all__ = [
    "capture_dom_baseline",
    "flatten_paste_text",
    "poll_read_reply",
    "poll_verify_committed",
]

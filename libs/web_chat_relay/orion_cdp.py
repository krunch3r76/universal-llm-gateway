"""Raw CDP helpers for chatgpt.com on io (:9342) when Playwright driver is stuck.

Uses the browser websocket + flattened ``Target.attachToTarget`` sessions so we
do not depend on ``connect_over_cdp`` or ``async_playwright().start()``.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from urllib.request import urlopen

DEFAULT_CDP_URL = "http://127.0.0.1:9342"


@dataclass(frozen=True)
class OrionCdpPage:
    href: str
    title: str
    body_len: int
    body_start: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "href": self.href,
            "title": self.title,
            "body_len": self.body_len,
            "body_start": self.body_start,
        }


def _browser_ws_url(cdp_url: str) -> str:
    origin = cdp_url.rstrip("/")
    with urlopen(f"{origin}/json/version", timeout=5) as resp:
        data = json.load(resp)
    ws = str(data.get("webSocketDebuggerUrl") or "").strip()
    if not ws:
        raise RuntimeError(f"no browser webSocketDebuggerUrl on {cdp_url}")
    return ws


async def _recv_id(ws: Any, mid: int, *, timeout: float) -> dict[str, Any]:
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout)
        msg = json.loads(raw)
        if msg.get("id") == mid:
            if msg.get("error"):
                raise RuntimeError(msg["error"])
            return msg


async def _send(
    ws: Any,
    mid: int,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    session_id: str | None = None,
) -> None:
    payload: dict[str, Any] = {"id": mid, "method": method, "params": params or {}}
    if session_id:
        payload["sessionId"] = session_id
    await ws.send(json.dumps(payload))


_PROBE_JS = """(() => ({
  href: location.href,
  title: document.title,
  body_len: (document.body && document.body.innerText || '').length,
  body_start: (document.body && document.body.innerText || '').slice(0, 1200),
}))()"""


async def probe_chatgpt_page(*, cdp_url: str = DEFAULT_CDP_URL) -> OrionCdpPage:
    """Return main chatgpt.com page text snapshot via flattened CDP session."""
    import websockets

    browser_ws = _browser_ws_url(cdp_url)
    async with websockets.connect(browser_ws, max_size=8_000_000, open_timeout=8) as ws:
        await _send(ws, 1, "Target.getTargets")
        targets = (await _recv_id(ws, 1, timeout=12))["result"]["targetInfos"]
        page = next(
            (t for t in targets if t.get("type") == "page" and "chatgpt.com" in (t.get("url") or "")),
            None,
        )
        if page is None:
            page = next((t for t in targets if t.get("type") == "page"), None)
        if page is None:
            raise RuntimeError("no page target on ChatGPT CDP")
        await _send(ws, 2, "Target.attachToTarget", {"targetId": page["targetId"], "flatten": True})
        sid = (await _recv_id(ws, 2, timeout=12))["result"]["sessionId"]
        await _send(
            ws,
            3,
            "Runtime.evaluate",
            {"expression": _PROBE_JS, "returnByValue": True},
            session_id=sid,
        )
        raw = (await _recv_id(ws, 3, timeout=20))["result"]["result"].get("value") or {}
        return OrionCdpPage(
            href=str(raw.get("href") or page.get("url") or ""),
            title=str(raw.get("title") or ""),
            body_len=int(raw.get("body_len") or 0),
            body_start=str(raw.get("body_start") or ""),
        )


async def refresh_connectors_diagnostic(*, cdp_url: str = DEFAULT_CDP_URL) -> dict[str, Any]:
    """Preflight for MCP connector automation (UI flow not wired until DOM is stable)."""
    snap = await probe_chatgpt_page(cdp_url=cdp_url)
    login_wall = snap.body_len < 80 or "log in" in snap.body_start.lower()
    blank_shell = snap.href.startswith("about:") or snap.body_len == 0
    return {
        "ok": False,
        "error": "renderer_not_ready" if blank_shell else ("login_required" if login_wall else "ui_automation_pending"),
        "cdp_url": cdp_url,
        "snapshot": snap.as_dict(),
        "hint": (
            "ChatGPT house must render chatgpt.com (not about:blank) on VNC :12 / CDP 9342. "
            "Then add MCP apps for https://mcp.askapharmd.me/mcp/life and /mcp/code under "
            "Settings → Apps (or Connectors). Host automation will mirror claude restore scripts."
        ),
    }
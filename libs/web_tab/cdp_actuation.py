"""CDP actuation transport — list/open targets and evaluate without focus steal."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlparse

from cursor_bridge.cdp_pages import list_pages

from web_tab.config import _validate_cdp_url
from web_tab.provider_selectors import match_tab_by_url


class CdpActuationPort(Protocol):
    """Mutating CDP surface for web-tab write ops."""

    def list_tabs(self, *, cdp_url: str) -> dict[str, Any]:
        """Return ``{pages, reachable, error}``."""

    def create_background_tab(self, *, cdp_url: str, url: str) -> dict[str, Any]:
        """Open ``url`` in a background target; never Page.bringToFront."""

    async def evaluate(
        self,
        *,
        ws_url: str,
        expression: str,
        await_promise: bool = False,
    ) -> dict[str, Any]:
        """Runtime.evaluate on a page debugger websocket."""


def _sync_evaluate(ws_url: str, expression: str, *, port: int) -> dict[str, Any]:
    import websocket

    conn = websocket.create_connection(
        ws_url,
        timeout=15,
        header=[f"Origin: http://127.0.0.1:{port}"],
    )
    try:
        conn.send(
            json.dumps(
                {
                    "id": 99,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": expression,
                        "awaitPromise": True,
                        "returnByValue": True,
                    },
                }
            )
        )
        while True:
            msg = json.loads(conn.recv())
            if msg.get("id") == 99:
                if "error" in msg:
                    return {"error": msg["error"]}
                value = ((msg.get("result") or {}).get("result") or {}).get("value")
                if isinstance(value, dict):
                    return value
                return {"raw": value}
    finally:
        conn.close()
    return {"error": {"code": "evaluate_incomplete"}}


def cdp_port_int(cdp_url: str) -> int:
    parts = urlparse(_validate_cdp_url(cdp_url))
    if parts.port is not None:
        return int(parts.port)
    if parts.scheme == "https":
        return 443
    return 80


def _browser_ws_url(cdp_url: str) -> str:
    import urllib.request

    port = cdp_port_int(cdp_url)
    endpoint = f"http://127.0.0.1:{port}/json/version"
    with urllib.request.urlopen(endpoint, timeout=5) as resp:  # noqa: S310
        payload = json.loads(resp.read())
    ws = payload.get("webSocketDebuggerUrl")
    if not isinstance(ws, str) or not ws.strip():
        raise OSError("no browser websocket on CDP host")
    return ws


def create_background_target(cdp_url: str, url: str) -> dict[str, Any]:
    """Target.createTarget with background=true, then navigate to *url*."""
    import websocket

    port = cdp_port_int(cdp_url)
    browser_ws = _browser_ws_url(cdp_url)
    conn = websocket.create_connection(
        browser_ws,
        timeout=10,
        header=[f"Origin: http://127.0.0.1:{port}"],
    )
    try:
        conn.send(
            json.dumps(
                {
                    "id": 1,
                    "method": "Target.createTarget",
                    "params": {"url": "about:blank", "background": True},
                }
            )
        )
        target_id = None
        while True:
            msg = json.loads(conn.recv())
            if msg.get("id") == 1:
                if "error" in msg:
                    return {"ok": False, "error": msg["error"]}
                result = msg.get("result") or {}
                target_id = result.get("targetId")
                break
        if not target_id:
            return {"ok": False, "error": {"code": "no_target_id"}}
        tid = str(target_id)
    finally:
        conn.close()

    pages = list_pages(cdp_url, timeout_s=8.0)
    row = next((p for p in pages if str(p.get("id")) == tid), None)
    if row is None:
        row = next(
            (p for p in pages if url.split("?", 1)[0] in str(p.get("url") or "")),
            None,
        )
    if row is None:
        return {"ok": False, "error": {"code": "target_not_listed"}, "target_id": tid}

    ws_url = str(row.get("web_socket_debugger_url") or "")
    if not ws_url:
        return {"ok": False, "error": {"code": "no_ws_url"}, "target_id": tid}

    nav = _sync_evaluate(
        ws_url,
        f"window.location.href = {json.dumps(url)}; true",
        port=port,
    )
    if nav.get("error"):
        return {
            "ok": False,
            "error": nav["error"],
            "target_id": tid,
            "web_socket_debugger_url": ws_url,
        }
    refreshed = list_pages(cdp_url, timeout_s=8.0)
    row2 = next((p for p in refreshed if str(p.get("id")) == tid), row)
    return {
        "ok": True,
        "target_id": tid,
        "url": str(row2.get("url") or url),
        "web_socket_debugger_url": str(row2.get("web_socket_debugger_url") or ws_url),
    }


class HttpCdpActuationPort:
    """Production actuation port."""

    def list_tabs(self, *, cdp_url: str) -> dict[str, Any]:
        try:
            cdp_url = _validate_cdp_url(cdp_url)
            pages = list_pages(cdp_url)
            return {"pages": pages, "reachable": True, "error": None}
        except Exception as exc:
            return {
                "pages": [],
                "reachable": False,
                "error": type(exc).__name__,
            }

    def create_background_tab(self, *, cdp_url: str, url: str) -> dict[str, Any]:
        cdp_url = _validate_cdp_url(cdp_url)
        listing = self.list_tabs(cdp_url=cdp_url)
        if not listing.get("reachable"):
            return {"ok": False, "error": {"code": "cdp_unreachable"}}
        pages = list(listing.get("pages") or [])
        existing = match_tab_by_url(pages, url)
        if existing and existing.get("id"):
            return {
                "ok": True,
                "reused": True,
                "target_id": str(existing["id"]),
                "url": str(existing.get("url") or url),
                "web_socket_debugger_url": str(
                    existing.get("web_socket_debugger_url") or ""
                ),
            }
        return create_background_target(cdp_url, url)

    async def evaluate(
        self,
        *,
        ws_url: str,
        expression: str,
        await_promise: bool = False,
    ) -> dict[str, Any]:
        from cursor_bridge.dom_composer_paste import _cdp_eval

        try:
            raw = await _cdp_eval(
                ws_url, expression, await_promise=await_promise, timeout_s=15.0
            )
        except Exception as exc:
            return {"error": {"code": "evaluate_failed", "type": type(exc).__name__}}
        if isinstance(raw, dict):
            return raw
        return {"raw": raw}


@dataclass
class RecordingActuationPort:
    """Test double recording actuation calls."""

    tabs: list[dict[str, str]] = field(default_factory=list)
    reachable: bool = True
    created: list[str] = field(default_factory=list)
    evaluate_script: dict[str, dict[str, Any]] = field(default_factory=dict)
    evaluate_calls: list[tuple[str, str]] = field(default_factory=list)
    create_results: dict[str, dict[str, Any]] = field(default_factory=dict)

    def list_tabs(self, *, cdp_url: str) -> dict[str, Any]:  # noqa: ARG002
        return {
            "pages": list(self.tabs),
            "reachable": self.reachable,
            "error": None if self.reachable else "down",
        }

    def create_background_tab(self, *, cdp_url: str, url: str) -> dict[str, Any]:
        key = url
        if key in self.create_results:
            return dict(self.create_results[key])
        existing = match_tab_by_url(self.tabs, url)
        if existing and existing.get("id"):
            return {
                "ok": True,
                "reused": True,
                "target_id": str(existing["id"]),
                "url": str(existing.get("url") or url),
                "web_socket_debugger_url": str(
                    existing.get("web_socket_debugger_url") or ""
                ),
            }
        self.created.append(url)
        tid = f"target-{len(self.created)}"
        row = {
            "id": tid,
            "url": url,
            "title": "",
            "web_socket_debugger_url": f"ws://fake/{tid}",
        }
        self.tabs.append(row)
        return {
            "ok": True,
            "reused": False,
            "target_id": tid,
            "url": url,
            "web_socket_debugger_url": row["web_socket_debugger_url"],
        }

    async def evaluate(
        self,
        *,
        ws_url: str,
        expression: str,
        await_promise: bool = False,  # noqa: ARG002
    ) -> dict[str, Any]:
        self.evaluate_calls.append((ws_url, expression))
        if ws_url in self.evaluate_script:
            return dict(self.evaluate_script[ws_url])
        for key, val in self.evaluate_script.items():
            if key in expression:
                return dict(val)
        return {"ok": True}


_DEFAULT = HttpCdpActuationPort()


def default_actuation_port() -> CdpActuationPort:
    return _DEFAULT

"""Injectable CDP observation port — list targets and evaluate JS without navigation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.error import URLError


class CdpObservationPort(Protocol):
    """Read-only CDP surface used by probe/status."""

    def list_tabs(self, *, cdp_url: str) -> dict[str, Any]:
        """Return ``{pages, reachable, error}`` for page targets."""

    async def evaluate_on_target(
        self,
        *,
        cdp_url: str,
        target_id: str,
        expression: str,
    ) -> dict[str, Any]:
        """Run *expression* on the page identified by *target_id*."""


@dataclass
class RecordingCdpPort:
    """Test double that records side-effect calls."""

    tabs: list[dict[str, str]] = field(default_factory=list)
    evaluate_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    navigated: list[str] = field(default_factory=list)
    clicks: list[str] = field(default_factory=list)
    reachable: bool = True
    list_error: str | None = None
    new_page_calls: int = 0

    def list_tabs(self, *, cdp_url: str) -> dict[str, Any]:  # noqa: ARG002
        return {
            "pages": list(self.tabs),
            "reachable": self.reachable,
            "error": self.list_error,
        }

    async def evaluate_on_target(
        self,
        *,
        cdp_url: str,  # noqa: ARG002
        target_id: str,
        expression: str,  # noqa: ARG002
    ) -> dict[str, Any]:
        if target_id in self.evaluate_results:
            return dict(self.evaluate_results[target_id])
        return {"url": "", "signed_in": None, "login_wall": None, "composer": None}


class HttpListCdpPort:
    """Production port: tab list via ``/json/list``; evaluate via Playwright attach."""

    async def evaluate_on_target(
        self,
        *,
        cdp_url: str,
        target_id: str,
        expression: str,
    ) -> dict[str, Any]:
        listed = self.list_tabs(cdp_url=cdp_url)
        if not listed.get("reachable"):
            return {"stale": True, "error": listed.get("error") or "cdp_unreachable"}
        tabs = listed.get("pages") or []
        tab_row = next((t for t in tabs if t.get("id") == target_id), None)
        if tab_row is None:
            return {"stale": True}

        want_url = (tab_row.get("url") or "").rstrip("/")
        if not want_url:
            return {"stale": True}

        from playwright.async_api import async_playwright

        pw = await async_playwright().start()
        try:
            browser = await pw.chromium.connect_over_cdp(cdp_url)
            page = None
            for ctx in browser.contexts:
                for candidate in ctx.pages:
                    if candidate.url.rstrip("/") == want_url:
                        page = candidate
                        break
                if page is not None:
                    break
            if page is None:
                return {"stale": True}
            raw = await page.evaluate(expression)
            if isinstance(raw, dict):
                return raw
            return {"raw": raw}
        finally:
            await pw.stop()

    def list_tabs(self, *, cdp_url: str) -> dict[str, Any]:
        from cursor_bridge.cdp_pages import list_pages

        try:
            pages = list_pages(cdp_url)
            return {"pages": pages, "reachable": True, "error": None}
        except (URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
            return {
                "pages": [],
                "reachable": False,
                "error": type(exc).__name__,
            }


_DEFAULT_PORT = HttpListCdpPort()


def default_cdp_port() -> CdpObservationPort:
    return _DEFAULT_PORT

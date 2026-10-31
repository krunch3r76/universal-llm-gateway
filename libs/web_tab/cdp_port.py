"""Injectable CDP observation port — list targets and evaluate JS without navigation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.error import URLError

from cursor_bridge.cdp_pages import list_pages


class CdpObservationPort(Protocol):
    """Read-only CDP surface used by probe/status."""

    def list_tabs(self, *, cdp_url: str) -> list[dict[str, str]]:
        """Return page targets (id, title, url)."""

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

    def list_tabs(self, *, cdp_url: str) -> list[dict[str, str]]:  # noqa: ARG002
        return list(self.tabs)

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
        from claude_bundles.skills_ui_panel import connect_cdp

        tab_row = next(
            (t for t in self.list_tabs(cdp_url=cdp_url) if t.get("id") == target_id),
            None,
        )
        pw, browser, _ctx, _default_page = await connect_cdp(cdp_url)
        try:
            page = _default_page
            want_url = (tab_row or {}).get("url") or ""
            if want_url:
                for ctx in browser.contexts:
                    for candidate in ctx.pages:
                        if candidate.url == want_url or (
                            want_url and want_url.rstrip("/") in candidate.url
                        ):
                            page = candidate
                            break
            raw = await page.evaluate(expression)
            if isinstance(raw, dict):
                return raw
            return {"raw": raw}
        finally:
            await browser.close()
            await pw.stop()

    def list_tabs(self, *, cdp_url: str) -> list[dict[str, str]]:
        try:
            return list_pages(cdp_url)
        except (URLError, OSError, TimeoutError, json.JSONDecodeError):
            return []


_DEFAULT_PORT = HttpListCdpPort()


def default_cdp_port() -> CdpObservationPort:
    return _DEFAULT_PORT

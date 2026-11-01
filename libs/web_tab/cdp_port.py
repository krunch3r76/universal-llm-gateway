"""Injectable CDP observation port — list targets and evaluate JS without navigation."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from urllib.error import URLError

from web_tab.config import (
    _validate_cdp_url,
    resolve_cdp_cleanup_timeout_s,
    resolve_cdp_evaluate_timeout_s,
)
from web_tab.surface_adapter import canonical_page_url, url_in_surface_scope


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
        tab_row: dict[str, Any] | None = None,
        surface: str = "chatgpt",
    ) -> dict[str, Any]:
        """Run *expression* on the listed page that matches *target_id* and URL."""


@dataclass
class RecordingCdpPort:
    """Test double that records side-effect calls."""

    tabs: list[dict[str, str]] = field(default_factory=list)
    evaluate_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    navigated: list[str] = field(default_factory=list)
    clicks: list[str] = field(default_factory=list)
    evaluated_targets: list[str] = field(default_factory=list)
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
        tab_row: dict[str, Any] | None = None,  # noqa: ARG002
        surface: str = "chatgpt",  # noqa: ARG002
    ) -> dict[str, Any]:
        self.evaluated_targets.append(target_id)
        if target_id in self.evaluate_results:
            return dict(self.evaluate_results[target_id])
        return {"url": "", "signed_in": None, "login_wall": None, "composer": None}


class HttpListCdpPort:
    """Production port: tab list via ``/json/list``; evaluate via Playwright attach.

    *connect* replaces ``connect_over_cdp`` in tests. Evaluate uses the listed
    *tab_row* and does not call ``list_tabs`` again. It refuses hosts outside
    *surface* (default ``chatgpt``), does not create pages, and does not close
    the browser.
    """

    def __init__(
        self,
        *,
        connect: Callable[[str], Awaitable[Any]] | None = None,
    ) -> None:
        self._connect = connect

    async def evaluate_on_target(
        self,
        *,
        cdp_url: str,
        target_id: str,
        expression: str,
        tab_row: dict[str, Any] | None = None,
        surface: str = "chatgpt",
    ) -> dict[str, Any]:
        if not isinstance(tab_row, dict) or str(tab_row.get("id") or "") != target_id:
            return {"stale": True, "error": "listing_mismatch"}
        want_url = str(tab_row.get("url") or "")
        if not want_url:
            return {"stale": True}
        if not url_in_surface_scope(surface, want_url):
            return {"error": {"code": "target_out_of_scope"}}

        timeout_s = resolve_cdp_evaluate_timeout_s()
        cleanup_s = resolve_cdp_cleanup_timeout_s()
        cleanup_state: dict[str, str | None] = {"outcome": None}

        async def _attach_evaluate() -> dict[str, Any]:
            browser, playwright = await self._open(
                cdp_url, cleanup_s=cleanup_s, cleanup_state=cleanup_state
            )
            try:
                page = await _page_matching_target(browser, want_url, target_id)
                if page is None:
                    return {"stale": True}
                raw = await page.evaluate(expression)
                if isinstance(raw, dict):
                    return raw
                return {"raw": raw}
            finally:
                if playwright is not None:
                    cleanup_state["outcome"] = await _bounded_playwright_stop(
                        playwright, timeout_s=cleanup_s
                    )

        try:
            return await asyncio.wait_for(_attach_evaluate(), timeout=timeout_s)
        except TimeoutError:
            err: dict[str, Any] = {"code": "cdp_timeout"}
            outcome = cleanup_state.get("outcome")
            if outcome is not None and outcome != "ok":
                err["cleanup"] = outcome
            return {"error": err}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            err = {
                "code": "cdp_attach_failed",
                "type": type(exc).__name__,
            }
            outcome = cleanup_state.get("outcome")
            if outcome is not None and outcome != "ok":
                err["cleanup"] = outcome
            return {"error": err}

    async def _open(
        self,
        cdp_url: str,
        *,
        cleanup_s: float | None = None,
        cleanup_state: dict[str, str | None] | None = None,
    ) -> tuple[Any, Any]:
        cdp_url = _validate_cdp_url(cdp_url)
        if self._connect is not None:
            return await self._connect(cdp_url), None
        from playwright.async_api import async_playwright

        stop_timeout = (
            cleanup_s if cleanup_s is not None else resolve_cdp_cleanup_timeout_s()
        )
        playwright = await async_playwright().start()
        try:
            browser = await playwright.chromium.connect_over_cdp(cdp_url)
        except BaseException:
            outcome = await _bounded_playwright_stop(playwright, timeout_s=stop_timeout)
            if cleanup_state is not None:
                cleanup_state["outcome"] = outcome
            raise
        return browser, playwright

    def list_tabs(self, *, cdp_url: str) -> dict[str, Any]:
        from cursor_bridge.cdp_pages import list_pages

        try:
            cdp_url = _validate_cdp_url(cdp_url)
        except ValueError as exc:
            return {
                "pages": [],
                "reachable": False,
                "error": str(exc),
            }

        try:
            pages = list_pages(cdp_url)
            return {"pages": pages, "reachable": True, "error": None}
        except (URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
            return {
                "pages": [],
                "reachable": False,
                "error": type(exc).__name__,
            }


def _consume_stop_task(task: asyncio.Task[Any]) -> None:
    try:
        task.result()
    except (asyncio.CancelledError, Exception):
        pass


async def _bounded_playwright_stop(
    playwright: Any,
    *,
    timeout_s: float,
) -> Literal["ok", "timeout", "error"]:
    """Stop Playwright within *timeout_s* without blocking on uncooperative cleanup."""
    if playwright is None:
        return "ok"
    stop_task = asyncio.create_task(playwright.stop())
    try:
        done, pending = await asyncio.wait({stop_task}, timeout=timeout_s)
    except asyncio.CancelledError:
        if not stop_task.done():
            stop_task.add_done_callback(_consume_stop_task)
        raise
    if stop_task in pending:
        stop_task.add_done_callback(_consume_stop_task)
        return "timeout"
    try:
        stop_task.result()
    except Exception:
        return "error"
    return "ok"


async def _target_id_of(page: Any) -> str | None:
    session = await page.context.new_cdp_session(page)
    info = await session.send("Target.getTargetInfo")
    if not isinstance(info, dict):
        return None
    target_info = info.get("targetInfo")
    if not isinstance(target_info, dict):
        return None
    target_id = target_info.get("targetId")
    return str(target_id) if isinstance(target_id, str) and target_id else None


async def _page_matching_target(
    browser: Any, want_url: str, target_id: str
) -> Any | None:
    """Return the page whose canonical URL and CDP target id both match.

    Same-URL twin tabs stay distinct. A target id that moved off the listed
    URL is not attached. DOM evaluation is the caller's next step.
    """
    want = canonical_page_url(want_url)
    for ctx in browser.contexts:
        for candidate in ctx.pages:
            if canonical_page_url(getattr(candidate, "url", "") or "") != want:
                continue
            try:
                found = await _target_id_of(candidate)
            except Exception:
                continue
            if found == target_id:
                return candidate
    return None


_DEFAULT_PORT = HttpListCdpPort()


def default_cdp_port() -> CdpObservationPort:
    """Return the process-wide production CDP port used when callers omit one."""
    return _DEFAULT_PORT

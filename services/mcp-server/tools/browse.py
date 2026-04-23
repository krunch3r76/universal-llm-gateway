"""Browse MCP tool — routes to the web-fetcher service for CDP-backed fetches.

Thin relay over HTTP to the ``WEB_FETCHER_URL`` FastAPI service
(``libs/web_fetcher/``). The web-fetcher attaches to a live Chrome/Chromium
over CDP for authenticated JavaScript-heavy workflows, click-triggered
downloads, and Cloudflare-challenged pages.

When ``screenshot=True``, this wrapper transparently writes the returned
image bytes into the MCP container's shared image dir (``MCP_SHARED_IMAGE_DIR``)
and surfaces ``screenshot_path`` in the response — the agent can feed that
path directly to ``view_image`` without scp'ing from the fetcher host.
"""

from __future__ import annotations

import base64
import hashlib
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from mcp.types import ImageContent, TextContent
from mcp_events import record

if TYPE_CHECKING:
    from fastmcp import FastMCP

_DEFAULT_MAX_CHARS = 36_000

_SANDBOX_ROOT = Path("/data/files")
_SHARED_IMAGE_DIR = Path(
    os.environ.get("MCP_SHARED_IMAGE_DIR", str(_SANDBOX_ROOT / ".shared-images"))
)
_SHARED_IMAGE_HOST_ROOT = Path(
    os.environ.get("MCP_SHARED_IMAGE_HOST_ROOT", str(_SHARED_IMAGE_DIR))
)


def _copy_screenshot_to_shared(img_b64: str, img_format: str) -> dict[str, str]:
    """Decode and persist a screenshot into the shared image dir.

    Returns a dict with ``screenshot_path`` (container-local, suitable for
    ``view_image``) and ``screenshot_host_path`` (where the file actually lives
    if MCP_SHARED_IMAGE_HOST_ROOT diverges from the in-container mount).
    """
    ext = "jpg" if img_format == "jpeg" else "png"
    img_bytes = base64.b64decode(img_b64)
    fingerprint = hashlib.sha256(img_bytes).hexdigest()[:16]
    ts = int(time.time())
    filename = f"browse-{ts}-{fingerprint}.{ext}"
    _SHARED_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    shared_path = _SHARED_IMAGE_DIR / filename
    shared_path.write_bytes(img_bytes)
    host_path = _SHARED_IMAGE_HOST_ROOT / filename
    return {
        "screenshot_path": str(shared_path),
        "screenshot_host_path": str(host_path),
    }


def register_browse_tool(mcp: FastMCP) -> None:
    """Register the browse tool on *mcp*."""

    @mcp.tool(title="Web Browse")
    def browse(
        url: str = "",
        selector: str | None = None,
        mode: str = "auto",
        max_chars: int = _DEFAULT_MAX_CHARS,
        raw: bool = False,
        screenshot: bool = False,
        screenshot_format: str = "jpeg",
        screenshot_quality: int = 80,
        screenshot_raw: bool = False,
        save_to: str | None = None,
        wait_for: dict[str, Any] | None = None,
        actions: list[dict[str, Any]] | None = None,
        save_screenshot_to: str | None = None,
        active_tab: bool = False,
        list_tabs: bool = False,
    ) -> list[TextContent | ImageContent] | dict[str, Any]:
        """Fetch a URL via a browser proxy with Cloudflare bypass and optional actions.

        Routes through a web-fetcher service running on a clean-IP host with a
        CDP-attached authenticated Chrome session. Use instead of web_fetch when
        the target site uses Cloudflare challenges, requires JavaScript
        rendering (SPAs), needs the user's live session, or requires clicking
        through UI to reach the content.

        list_tabs — enumerate all open tabs:
          When True, returns {method: "list_tabs", tabs: [{index, title, url,
          visible, focused}, ...]}. Use to identify which tab to interact with
          or to confirm which tab is active before using active_tab.
          Mutually exclusive with url, active_tab.

        active_tab — read the currently-focused tab without navigating:
          When True, ``url`` is ignored and the tool reads the title/URL/DOM
          (and optional screenshot) of whichever http(s) tab the user has
          focused on the CDP-attached Chrome. Use when the user says
          "look at the page I'm on" or "what am I looking at right now".
          Composable with ``actions`` (run click/fill sequences on the active
          tab before extracting), ``selector``, and ``screenshot``.
          Mutually exclusive with ``url``, ``save_to``, ``wait_for``.
          The response includes ``method: "active_tab"`` for confirmation.

        Modes:
          auto (default): Try plain HTTP first, fall back to headless Chromium
            if Cloudflare challenge detected or JS required.
          browser: Always use Chromium via CDP (slower, handles all JS/CF/auth).
          http: Plain HTTP only through the proxy host's IP (no browser).

        wait_for — gate page readiness after initial navigation:
          {"type": "selector", "value": "css-selector", "timeout_ms": 10000}
          {"type": "networkidle", "timeout_ms": 15000}
          {"type": "timeout_ms", "value": 3000}
          Applied once before actions run. Required for React/Vue/Angular SPAs
          where domcontentloaded fires before hydration.

        actions — server-side sequential browser actions before extraction:
          [
            {"type": "click", "selector": "..."},
            {"type": "fill", "selector": "input", "value": "..."},
            {"type": "press", "key": "Enter"},
            {"type": "select_option", "selector": "select", "value": "..."},
            {"type": "hover", "selector": "..."},
            {"type": "wait_for_selector", "selector": "...", "timeout_ms": 5000},
            {"type": "wait_for_networkidle", "timeout_ms": 10000},
            {"type": "wait_for_timeout", "timeout_ms": 2000}
          ]
          Executed atomically on a single page. If an action fails, the response
          includes ``action_failure: {error, failed_at, last_url}``.

        save_to + actions — click-triggered downloads:
          When save_to and actions are both set, the action sequence is wrapped
          in page.expect_download(). Whichever action triggers a download event
          (typical pattern: click a "Download PDF" button) captures the bytes
          to save_to. Returns {saved_to, size, url} — no content field.

        save_to without actions — direct-URL download (existing behavior):
          Navigate to url, capture browser download event or inline response.

        screenshot + save_screenshot_to:
          save_screenshot_to writes the screenshot to an absolute path on the
          web-fetcher host (e.g. /tmp/foo.png on Jupiter). Optional.
          This wrapper ALSO auto-copies the screenshot into the MCP container's
          shared image dir and surfaces ``screenshot_path`` in the response.
          Default agent workflow:
            browse(url, screenshot=True)
              → {..., "screenshot_path": "/data/files/.shared-images/..."}
            view_image(path="/data/files/.shared-images/...")

        Args:
            url: http(s) URL to fetch. Required unless ``active_tab=True``.
            selector: Optional CSS selector — extract only matching elements.
            mode: "auto" (default), "browser", or "http".
            max_chars: Maximum characters to return (default 36000).
            raw: If True, return raw HTML instead of extracted text.
            screenshot: If True, capture a full-page screenshot.
            screenshot_format: "jpeg" (default, smaller) or "png" (lossless).
            screenshot_quality: JPEG quality 1-100 (default 80). Ignored for PNG.
            screenshot_raw: If True, embed screenshot as base64 in JSON dict
                instead of returning ImageContent (for non-Claude MCP clients).
                Auto-copy to shared image dir still happens regardless.
            save_to: Absolute path on the web-fetcher host to save a downloaded
                file. Without actions → direct navigate+capture. With actions
                → capture a click-triggered download.
            wait_for: Page readiness gate (see above).
            actions: Sequential browser actions (see above).
            save_screenshot_to: Absolute path on the web-fetcher host for a
                persistent screenshot copy (optional).
            active_tab: If True, read the currently-focused tab from the
                CDP-attached Chrome instead of navigating to ``url``.
            list_tabs: If True, enumerate all open http(s) tabs and return
                their title, URL, index, visibility, and focus state.

        Returns:
            list_tabs=True: {method: "list_tabs", tabs: [{index, title, url,
                visible, focused}, ...]}
            save_to captured: {saved_to, size, url}
            screenshot=True without screenshot_raw (default):
              [TextContent (json of {url, title, content, screenshot_path, ...}),
               ImageContent]
            screenshot=True with screenshot_raw=True:
              {..., screenshot, screenshot_format, screenshot_path}
            Normal fetch: {url, title, content, total_chars, truncated, method,
                cf_bypassed, [action_failure]}
        """
        fetcher_url = os.environ.get("WEB_FETCHER_URL", "").strip()
        if not fetcher_url:
            return {
                "error": (
                    "WEB_FETCHER_URL not configured. Start the web-fetcher service "
                    "on a clean-IP host and set WEB_FETCHER_URL=http://HOST:PORT "
                    "in the MCP server environment."
                ),
                "url": url,
            }

        if not active_tab and not list_tabs and not url:
            return {
                "error": "url is required unless active_tab=True or list_tabs=True",
                "url": "",
            }

        # Downloads and multi-step action sequences may take longer than normal fetches.
        client_timeout = 120.0 if (save_to or actions) else 45.0

        try:
            with httpx.Client(timeout=client_timeout) as client:
                resp = client.post(
                    f"{fetcher_url.rstrip('/')}/fetch",
                    json={
                        "url": url,
                        "selector": selector,
                        "mode": mode,
                        "max_chars": max_chars,
                        "raw": raw,
                        "screenshot": screenshot,
                        "screenshot_format": screenshot_format,
                        "screenshot_quality": screenshot_quality,
                        "save_to": save_to,
                        "wait_for": wait_for,
                        "actions": actions,
                        "save_screenshot_to": save_screenshot_to,
                        "active_tab": active_tab,
                        "list_tabs": list_tabs,
                    },
                )
                resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            record(
                "mcp.browse.fetch.failed",
                url=url,
                kind="http_status",
                status=exc.response.status_code,
                error=str(exc),
            )
            return {
                "error": f"Fetcher HTTP {exc.response.status_code}",
                "url": url,
            }
        except httpx.RequestError as exc:
            record(
                "mcp.browse.fetch.failed", url=url, kind="unreachable", error=str(exc)
            )
            return {"error": f"Fetcher unreachable: {exc}", "url": url}

        data = resp.json()

        # save_to mode (direct or click-triggered) — return download metadata as-is.
        if save_to is not None and "saved_to" in data:
            record(
                "mcp.browse.download.completed",
                url=url,
                saved_to=data.get("saved_to"),
                size=data.get("size"),
            )
            return data

        record(
            "mcp.browse.fetch.completed",
            url=url,
            total_chars=data.get("total_chars"),
            method=data.get("method"),
            cf_bypassed=data.get("cf_bypassed"),
            screenshot=screenshot,
            actions=bool(actions),
        )

        img_b64: str | None = data.pop("screenshot", None)
        img_fmt: str = data.pop("screenshot_format", screenshot_format)

        if screenshot and img_b64:
            try:
                shared_info = _copy_screenshot_to_shared(img_b64, img_fmt)
                data["screenshot_path"] = shared_info["screenshot_path"]
                data["screenshot_host_path"] = shared_info["screenshot_host_path"]
            except Exception as exc:
                record("mcp.browse.screenshot.failed", error=str(exc))

        if screenshot and img_b64 and not screenshot_raw:
            mime = "image/jpeg" if img_fmt == "jpeg" else "image/png"
            import json as _json

            return [
                TextContent(type="text", text=_json.dumps(data)),
                ImageContent(type="image", data=img_b64, mimeType=mime),
            ]

        if screenshot and img_b64 and screenshot_raw:
            data["screenshot"] = img_b64
            data["screenshot_format"] = img_fmt

        return data

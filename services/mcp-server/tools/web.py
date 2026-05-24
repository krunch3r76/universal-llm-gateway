"""Web search and fetch tools — Brave Search API + trafilatura extraction.

Web search requires BRAVE_SEARCH_API_KEY environment variable.
Web fetch works independently — extracts readable content from any URL
with configurable truncation to manage context budget.

The browse tool (CDP-backed, authenticated, supports wait_for/actions/downloads)
lives in ``tools/browse.py`` and is registered via ``register_browse_tool``.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import socket
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx
import trafilatura

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_BRAVE_API_URL = "https://api.search.brave.com/res/v1/web/search"
_BRAVE_KEY_ENV = "BRAVE_SEARCH_API_KEY"
_FETCH_TIMEOUT = 15.0
_SEARCH_TIMEOUT = 10.0
_DEFAULT_MAX_CHARS = 36_000
_USER_AGENT = "Mozilla/5.0 (compatible; MCPFetcher/1.0; +https://mcp.k-1.me)"


def is_private_url(url: str) -> bool:
    """Return True if *url* targets a private/loopback address (SSRF guard)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return True
    hostname = parsed.hostname
    if not hostname:
        return True
    if hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return True
    try:
        for _family, _type, _proto, _canonname, sockaddr in socket.getaddrinfo(
            hostname, None, type=socket.SOCK_STREAM
        ):
            addr = sockaddr[0]
            if ipaddress.ip_address(addr).is_private:
                return True
    except (socket.gaierror, ValueError):
        return True
    return False


def _extract_title(html: str) -> str:
    """Extract the <title> tag content from raw HTML."""
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else ""


def register_web_tools(mcp: FastMCP) -> None:
    """Register web search and fetch tools on *mcp*."""

    @mcp.tool(title="Web Search")
    def web_search(
        query: str,
        max_results: int = 5,
    ) -> dict[str, list[dict[str, str]] | str]:
        """Search the web via Brave Search API. Returns {results: [{title, url, snippet, age}]}."""
        api_key = os.environ.get(_BRAVE_KEY_ENV, "").strip()
        if not api_key:
            return {
                "error": "BRAVE_SEARCH_API_KEY not configured. Web search is unavailable."
            }

        max_results = max(1, min(20, max_results))
        try:
            with httpx.Client(timeout=_SEARCH_TIMEOUT) as client:
                resp = client.get(
                    _BRAVE_API_URL,
                    headers={
                        "X-Subscription-Token": api_key,
                        "Accept": "application/json",
                    },
                    params={"q": query, "count": max_results},
                )
                resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.warning("Brave API HTTP error: %s", e)
            return {"error": f"Brave search failed: HTTP {e.response.status_code}"}
        except httpx.RequestError as e:
            logger.warning("Brave API request error: %s", e)
            return {"error": f"Brave search request failed: {e}"}

        data = resp.json()
        web_results = data.get("web", {}).get("results", [])

        results = []
        for item in web_results[:max_results]:
            results.append(
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "snippet": item.get("description", ""),
                    "age": item.get("age", ""),
                }
            )

        logger.info("web_search: query=%r → %d results", query, len(results))
        return {
            "results": results,
            "_next": (
                "If these search results contain novel facts about a known "
                "Cortex entity, seed via cortex assert with derivation_type "
                '"direct_observation" and the source URL as evidence_uri'
            ),
        }

    @mcp.tool(title="Web Fetch")
    def web_fetch(
        url: str,
        max_chars: int = _DEFAULT_MAX_CHARS,
        start_offset: int = 0,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        body: str | None = None,
        raw: bool = False,
    ) -> dict[str, str | int | bool]:
        """Fetch a URL and return its content.

        For HTML pages, strips boilerplate via trafilatura and returns clean text.
        For JSON APIs (or when raw=True), returns the response body as-is.

        Use headers to pass authentication (Cookie, Authorization, etc.).
        Use method + body for POST requests (e.g. GraphQL APIs).

        Args:
            url: The URL to fetch (must be http or https, no private IPs).
            max_chars: Maximum characters to return (default 36000).
            start_offset: Character offset for pagination (default 0).
            method: HTTP method — "GET" (default) or "POST".
            headers: Optional dict of extra request headers. The User-Agent is
                set automatically but can be overridden. Example:
                {"Cookie": "token=abc; session=xyz", "Authorization": "Bearer abc"}
            body: Optional request body string for POST requests.
                For JSON APIs pass a JSON string; the Content-Type header is
                NOT set automatically — include it in headers if needed.
            raw: If True, skip trafilatura extraction and return the raw response
                body. Use for JSON APIs or non-HTML content (default False).

        Returns:
            {"title", "content", "url", "total_chars", "truncated"}
            or on error: {"error", "url", "status_code"}
        """
        if is_private_url(url):
            return {
                "error": "URL targets a private or loopback address. Blocked for security.",
                "url": url,
            }

        method = method.upper()
        request_headers = {"User-Agent": _USER_AGENT}
        if headers:
            request_headers.update(headers)

        try:
            with httpx.Client(
                timeout=_FETCH_TIMEOUT,
                follow_redirects=True,
            ) as client:
                if method == "POST":
                    resp = client.post(url, headers=request_headers, content=body or "")
                else:
                    resp = client.get(url, headers=request_headers)
                resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.warning("web_fetch HTTP error for %s: %s", url, e)
            return {
                "error": f"HTTP {e.response.status_code}",
                "url": url,
                "status_code": e.response.status_code,
            }
        except httpx.RequestError as e:
            logger.warning("web_fetch request error for %s: %s", url, e)
            return {"error": f"Request failed: {e}", "url": url}

        response_text = resp.text
        content_type = resp.headers.get("content-type", "")
        is_html = "html" in content_type

        if raw or not is_html:
            content = response_text
            title = ""
            warning = ""
        else:
            title = _extract_title(response_text)
            extracted = trafilatura.extract(
                response_text,
                include_links=True,
                include_tables=True,
                output_format="txt",
            )
            if extracted is None:
                content = response_text
                warning = " (trafilatura extraction failed — raw HTML)"
            else:
                content = extracted
                warning = ""

        total_chars = len(content)
        sliced = content[start_offset : start_offset + max_chars]
        truncated = (start_offset + max_chars) < total_chars

        logger.info(
            "web_fetch: %s %s → %d total chars, returning %d (offset=%d)",
            method,
            url,
            total_chars,
            len(sliced),
            start_offset,
        )

        return {
            "title": title,
            "content": sliced + (warning if not raw and is_html else ""),
            "url": str(resp.url),
            "total_chars": total_chars,
            "truncated": truncated,
        }

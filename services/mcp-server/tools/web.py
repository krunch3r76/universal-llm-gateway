"""Web search and fetch tools — Brave Search API + trafilatura extraction.

Web search requires BRAVE_SEARCH_API_KEY environment variable.
Web fetch works independently — extracts readable content from any URL
with configurable truncation to manage context budget.
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


def _is_private_url(url: str) -> bool:
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

    @mcp.tool()
    def web_search(
        query: str,
        max_results: int = 5,
    ) -> dict[str, list[dict[str, str]] | str]:
        """Search the web using Brave Search API.

        Returns a list of results with title, URL, snippet, and age.
        Requires BRAVE_SEARCH_API_KEY to be configured.

        Args:
            query: Search query string.
            max_results: Maximum number of results (1-20, default 5).

        Returns:
            {"results": [{"title", "url", "snippet", "age"}, ...]}
        """
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
        return {"results": results}

    @mcp.tool()
    def web_fetch(
        url: str,
        max_chars: int = _DEFAULT_MAX_CHARS,
        start_offset: int = 0,
    ) -> dict[str, str | int | bool]:
        """Fetch a web page and extract its readable content.

        Uses trafilatura to strip boilerplate (ads, navigation, etc.) and
        return clean text. Typical 2MB HTML page becomes 5-50KB of content.

        Use max_chars and start_offset for pagination on large pages.

        Args:
            url: The URL to fetch (must be http or https).
            max_chars: Maximum characters to return (default 30000).
            start_offset: Character offset to start from (default 0, for pagination).

        Returns:
            {"title", "content", "url", "total_chars", "truncated"}
        """
        if _is_private_url(url):
            return {
                "error": "URL targets a private or loopback address. Blocked for security.",
                "url": url,
            }

        try:
            with httpx.Client(
                timeout=_FETCH_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": _USER_AGENT},
            ) as client:
                resp = client.get(url)
                resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.warning("web_fetch HTTP error for %s: %s", url, e)
            return {"error": f"HTTP {e.response.status_code}", "url": url}
        except httpx.RequestError as e:
            logger.warning("web_fetch request error for %s: %s", url, e)
            return {"error": f"Request failed: {e}", "url": url}

        html = resp.text
        title = _extract_title(html)

        extracted = trafilatura.extract(
            html,
            include_links=True,
            include_tables=True,
            output_format="txt",
        )

        if extracted is None:
            content = html[:max_chars]
            warning = " (trafilatura extraction failed — raw HTML truncated)"
        else:
            content = extracted
            warning = ""

        total_chars = len(content)
        sliced = content[start_offset : start_offset + max_chars]
        truncated = (start_offset + max_chars) < total_chars

        logger.info(
            "web_fetch: %s → %d total chars, returning %d (offset=%d)",
            url,
            total_chars,
            len(sliced),
            start_offset,
        )

        return {
            "title": title,
            "content": sliced + warning,
            "url": url,
            "total_chars": total_chars,
            "truncated": truncated,
        }

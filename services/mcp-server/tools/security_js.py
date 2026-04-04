"""JS bundle analysis tool for security testing — regex-based extraction
of API endpoints, secrets, auth patterns, and interesting code patterns.

Split from security.py for SLOC compliance. Uses _exec_http from security
module for URL fetching.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from tools.security import _exec_http

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_security_js_tools(mcp: FastMCP) -> None:
    """Register JS analysis tools on *mcp*."""

    @mcp.tool()
    def js_analyze(
        source: str,
        extractors: list[str] | None = None,
    ) -> dict[str, Any]:
        """Extract security-relevant artifacts from JavaScript bundles.

        source: URL to fetch, or raw JS (>500 chars treated as raw JS).
        extractors (default all): api_endpoints, hardcoded_secrets, auth_logic,
            interesting_patterns, source_maps.
        """
        if len(source) <= 500 and source.startswith(("http://", "https://")):
            resp = _exec_http("GET", source, timeout_ms=30000)
            if "error" in resp:
                return {"error": f"Fetch failed: {resp['error']}"}
            js = resp["body"]
        else:
            js = source

        all_ext = {
            "api_endpoints",
            "hardcoded_secrets",
            "auth_logic",
            "interesting_patterns",
            "source_maps",
        }
        active = (set(extractors) & all_ext) if extractors else all_ext
        out: dict[str, Any] = {"source_size": len(js)}

        if "api_endpoints" in active:
            _extract_endpoints(js, out)
        if "hardcoded_secrets" in active:
            _extract_secrets(js, out)
        if "auth_logic" in active:
            _extract_auth(js, out)
        if "interesting_patterns" in active:
            _extract_interesting(js, out)
        if "source_maps" in active:
            out["source_map_urls"] = re.findall(r"//[#@]\s*sourceMappingURL=(\S+)", js)
        return out


def _extract_endpoints(js: str, out: dict[str, Any]) -> None:
    """Extract API endpoint URLs from JS source."""
    eps: list[dict[str, str]] = []
    for m in re.finditer(
        r"""["'`]((?:https?://|/api/|/v\d+/|/auth/|/user)[^"'`\s]{3,})["'`]""", js
    ):
        eps.append(
            {"url": m.group(1), "context": js[max(0, m.start() - 20) : m.end() + 20]}
        )
    for m in re.finditer(
        r"""(?:fetch|axios\.(?:get|post|put|delete|patch))\(\s*["'`]([^"'`]+)["'`]""",
        js,
    ):
        eps.append({"url": m.group(1), "method": "fetch/axios"})
    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for e in eps:
        if e["url"] not in seen:
            seen.add(e["url"])
            unique.append(e)
    out["api_endpoints"] = unique[:200]


def _extract_secrets(js: str, out: dict[str, Any]) -> None:
    """Extract potential hardcoded secrets from JS source."""
    secs: list[dict[str, str]] = []
    for pat, typ in [
        (
            r"""["'](?:sk|pk|api|key|token|secret|password|auth)[-_]?\w{16,}["']""",
            "potential_key",
        ),
        (r"""["'][A-Za-z0-9+/]{40,}={0,2}["']""", "base64_blob"),
        (
            r"""(?:api[_-]?key|apikey|auth[_-]?token|secret[_-]?key)\s*[:=]\s*["']([^"']+)["']""",
            "named_secret",
        ),
    ]:
        for m in re.finditer(pat, js, re.IGNORECASE):
            secs.append(
                {
                    "type": typ,
                    "value": m.group(0)[:100],
                    "location": f"char {m.start()}",
                }
            )
    out["secrets"] = secs[:50]


def _extract_auth(js: str, out: dict[str, Any]) -> None:
    """Extract auth-related patterns from JS source."""
    auths: list[dict[str, str]] = []
    for pat, desc in [
        (
            r"""localStorage\.(?:get|set)Item\(\s*["']([^"']*(?:token|auth|session|jwt)[^"']*)["']""",
            "localStorage",
        ),
        (r"""(?:Bearer|Basic)\s+\S+""", "auth_header"),
        (r"""document\.cookie\s*=""", "cookie_set"),
        (r"""(?:jwt|jsonwebtoken|jose)\.(?:sign|verify|decode)""", "jwt_op"),
    ]:
        for m in re.finditer(pat, js, re.IGNORECASE):
            snippet = js[max(0, m.start() - 30) : min(len(js), m.end() + 30)]
            auths.append(
                {
                    "description": desc,
                    "code_snippet": snippet,
                    "location": f"char {m.start()}",
                }
            )
    out["auth_patterns"] = auths[:50]


def _extract_interesting(js: str, out: dict[str, Any]) -> None:
    """Extract interesting security-relevant patterns from JS source."""
    items: list[dict[str, str]] = []
    for pat, name in [
        (r"""\beval\s*\(""", "eval"),
        (r"""\.innerHTML\s*=""", "innerHTML"),
        (r"""postMessage\s*\(""", "postMessage"),
        (r"""(?:crypto|CryptoJS)\.\w+""", "crypto"),
        (r"""new\s+Function\s*\(""", "Function_ctor"),
        (r"""document\.write\(""", "doc_write"),
    ]:
        for m in re.finditer(pat, js):
            snippet = js[max(0, m.start() - 20) : min(len(js), m.end() + 20)]
            items.append(
                {
                    "pattern": name,
                    "code_snippet": snippet,
                    "location": f"char {m.start()}",
                }
            )
    out["interesting"] = items[:100]

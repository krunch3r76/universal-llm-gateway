"""Security testing tools — HTTP client, session store, response diffing,
and traffic replay for authenticated bounty testing.

Business logic implemented directly (approved exception to MCP relay
architecture — no backing REST service for raw HTTP testing).

JS bundle analysis lives in security_js.py (SLOC split).
"""

from __future__ import annotations

import base64
import difflib
import json
import logging
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx

from tools.web import is_private_url

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_store: dict[str, tuple[str, float]] = {}
_DEFAULT_TTL = 1200

_HTTP_PARAMS = frozenset(
    {
        "method",
        "url",
        "headers",
        "body",
        "follow_redirects",
        "timeout_ms",
        "session_profile",
    }
)


def _prune_expired() -> None:
    """Remove expired entries from the session store."""
    now = time.time()
    for k in [k for k, (_, exp) in _store.items() if exp <= now]:
        del _store[k]


def _profile_headers(profile: str) -> dict[str, str]:
    """Return headers for session keys matching ``{profile}/*``."""
    _prune_expired()
    prefix = f"{profile}/"
    now = time.time()
    return {
        k[len(prefix) :]: v
        for k, (v, exp) in _store.items()
        if k.startswith(prefix) and exp > now
    }


def exec_http(
    method: str = "GET",
    url: str = "",
    headers: dict[str, str] | None = None,
    body: str | None = None,
    follow_redirects: bool = True,
    timeout_ms: int = 10000,
    session_profile: str | None = None,
) -> dict[str, Any]:
    """Core HTTP execution shared by http_request, http_diff, http_replay."""
    if is_private_url(url):
        return {
            "error": "URL targets a private/loopback address — blocked.",
            "url": url,
        }

    merged: dict[str, str] = {}
    if session_profile:
        merged.update(_profile_headers(session_profile))
    if headers:
        merged.update(headers)

    t0 = time.monotonic()
    try:
        with httpx.Client(
            timeout=timeout_ms / 1000.0, follow_redirects=follow_redirects
        ) as client:
            resp = client.request(
                method.upper(), url, headers=merged or None, content=body
            )
    except httpx.RequestError as exc:
        ms = round((time.monotonic() - t0) * 1000)
        return {"error": f"Request failed: {exc}", "url": url, "timing_ms": ms}

    ms = round((time.monotonic() - t0) * 1000)
    ct = resp.headers.get("content-type", "")
    is_text = any(t in ct for t in ("text/", "json", "xml", "javascript", "html"))

    if is_text:
        resp_body = resp.text
    else:
        b64 = base64.b64encode(resp.content).decode()
        resp_body = f"[base64:{len(resp.content)}B] {b64}"

    return {
        "status": resp.status_code,
        "headers": dict(resp.headers),
        "body": resp_body,
        "timing_ms": ms,
        "url": str(resp.url),
    }


def _set_nested(obj: dict[str, Any], dotpath: str, value: Any) -> None:
    """Set a value at a dot-notation path (e.g. ``order.amount``)."""
    parts = dotpath.split(".")
    for p in parts[:-1]:
        obj = obj.setdefault(p, {})
    obj[parts[-1]] = value


def _norm_json(text: str) -> str:
    """Normalize JSON for diffing — sort keys, pretty-print."""
    try:
        return json.dumps(json.loads(text), sort_keys=True, indent=2)
    except (json.JSONDecodeError, TypeError):
        return text


def register_security_tools(mcp: FastMCP) -> None:
    """Register security testing tools on *mcp*."""

    @mcp.tool(title="Security: Session Store")
    def session_store(
        op: str,
        key: str = "",
        value: str = "",
        ttl_seconds: int = _DEFAULT_TTL,
    ) -> dict[str, Any]:
        """In-memory credential store for security testing sessions.

        Set credentials with ``{profile}/{Header-Name}`` keys (e.g.
        ``blockchain/Authorization``), then pass ``session_profile="blockchain"``
        to http_request / http_diff / http_replay to auto-inject matching headers.

        Ops: set (store with TTL, default 20min), get, list, delete.
        Never written to disk — in-memory only, lost on MCP restart.
        """
        _prune_expired()
        if op == "set":
            if not key:
                return {"error": "key required"}
            exp = time.time() + ttl_seconds
            _store[key] = (value, exp)
            return {"key": key, "expires_at": exp, "ttl_seconds": ttl_seconds}
        if op == "get":
            if not key:
                return {"error": "key required"}
            entry = _store.get(key)
            if not entry:
                return {"error": f"Key {key!r} not found"}
            return {"key": key, "value": entry[0], "expires_at": entry[1]}
        if op == "list":
            return {"keys": sorted(_store)}
        if op == "delete":
            return {"deleted": key, "found": _store.pop(key, None) is not None}
        return {"error": f"Unknown op {op!r}. Use: set, get, list, delete"}

    @mcp.tool(title="HTTP Request")
    def http_request(
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        body: str | None = None,
        follow_redirects: bool = True,
        timeout_ms: int = 10000,
        session_profile: str | None = None,
    ) -> dict[str, Any]:
        """Raw HTTP client for security testing — full response headers, all methods, timing.

        Unlike web_fetch (content extraction), returns raw status, headers, body
        for security analysis. Use session_profile to auto-inject stored credentials.
        Binary responses returned as base64. Private/loopback addresses blocked.
        """
        return exec_http(
            method=method,
            url=url,
            headers=headers,
            body=body,
            follow_redirects=follow_redirects,
            timeout_ms=timeout_ms,
            session_profile=session_profile,
        )

    @mcp.tool(title="HTTP Diff")
    def http_diff(
        request_a: dict[str, Any],
        request_b: dict[str, Any],
        diff_mode: str = "full",
    ) -> dict[str, Any]:
        """Compare two HTTP requests side-by-side for IDOR / access control testing.

        Each request: {method, url, headers?, body?, session_profile?}.
        diff_mode: full | status_only | body_only | headers_only.
        JSON bodies are sorted before diffing for semantic comparison.
        """
        ra = exec_http(**{k: v for k, v in request_a.items() if k in _HTTP_PARAMS})
        if "error" in ra:
            return {"error": f"Request A: {ra['error']}"}
        rb = exec_http(**{k: v for k, v in request_b.items() if k in _HTTP_PARAMS})
        if "error" in rb:
            return {"error": f"Request B: {rb['error']}"}

        result: dict[str, Any] = {
            "status_match": ra["status"] == rb["status"],
            "status_a": ra["status"],
            "status_b": rb["status"],
        }

        if diff_mode in ("full", "headers_only"):
            ha, hb = ra["headers"], rb["headers"]
            result["header_diffs"] = [
                {"key": k, "value_a": ha.get(k), "value_b": hb.get(k)}
                for k in sorted(set(ha) | set(hb))
                if ha.get(k) != hb.get(k)
            ]

        if diff_mode in ("full", "body_only"):
            na, nb = _norm_json(ra["body"]), _norm_json(rb["body"])
            result["body_identical"] = na == nb
            if na != nb:
                cap = 50_000
                result["body_diff"] = "".join(
                    difflib.unified_diff(
                        na[:cap].splitlines(keepends=True),
                        nb[:cap].splitlines(keepends=True),
                        fromfile="response_a",
                        tofile="response_b",
                    )
                )
                if len(na) > cap or len(nb) > cap:
                    result["body_truncated"] = True
            else:
                result["body_diff"] = ""

        if not result.get("status_match", True):
            summary = f"Status differs ({ra['status']} vs {rb['status']})"
        elif result.get("body_identical", True):
            summary = "Responses identical"
        else:
            summary = f"Same status {ra['status']}, bodies differ"
        result["summary"] = summary
        return result

    @mcp.tool(title="HTTP Replay")
    def http_replay(
        captured_request: dict[str, Any],
        modifications: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Replay a captured request with modifications for parameter tampering.

        captured_request: {method, url, headers, postData?} (CDP format).
        modifications: {headers?, url_params?, body_params? (dot-notation), session_profile?}.
        """
        method = captured_request.get("method", "GET")
        url = captured_request.get("url", "")
        hdrs = dict(captured_request.get("headers", {}))
        body = captured_request.get("postData")
        mods = modifications or {}

        if mods.get("headers"):
            hdrs.update(mods["headers"])

        if mods.get("url_params"):
            parsed = urlparse(url)
            params = parse_qs(parsed.query, keep_blank_values=True)
            for k, v in mods["url_params"].items():
                params[k] = [v]
            url = urlunparse(parsed._replace(query=urlencode(params, doseq=True)))

        if mods.get("body_params") and body:
            try:
                obj = json.loads(body)
                for path, val in mods["body_params"].items():
                    _set_nested(obj, path, val)
                body = json.dumps(obj)
            except (json.JSONDecodeError, TypeError):
                logger.warning(
                    "http_replay: body_params skipped — body is not valid JSON"
                )

        return exec_http(
            method=method,
            url=url,
            headers=hdrs,
            body=body,
            session_profile=mods.get("session_profile"),
        )

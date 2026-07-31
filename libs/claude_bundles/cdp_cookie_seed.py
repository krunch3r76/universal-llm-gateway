"""Session-cookie transfer from the primary Chrome into a freshly launched lane.

``cdp_lane._seed_profile`` rsyncs ``PRIMARY_PROFILE`` off disk, but Chrome does
not flush its cookie store while running: the primary's ``Default/Cookies`` is a
4 KB empty SQLite shell while the live browser holds a valid ``sessionKey`` in
memory. Every lane seeded from disk is therefore born signed out, lands on
``/login?from=logout``, fails ``ensure_cowork_auto``, and leaks its Chrome.

Reading cookies over CDP instead of off disk is immune to flush timing. The
browser-level ``Storage`` domain is used rather than ``Network`` so no page
target has to exist on either end.
"""

from __future__ import annotations

import json
import urllib.request

import websocket

PRIMARY_CDP_PORT = 9222
COOKIE_DOMAIN_SUFFIX = "claude.ai"

# Storage.setCookies takes CookieParam, a strict subset of the Cookie objects
# Storage.getCookies returns — `size` and `session` are read-only and rejected.
_COOKIE_PARAM_FIELDS = (
    "name",
    "value",
    "domain",
    "path",
    "secure",
    "httpOnly",
    "sameSite",
    "expires",
    "priority",
    "sameParty",
    "sourceScheme",
    "sourcePort",
    "partitionKey",
)

_RPC_TIMEOUT_S = 15.0


class CookieSeedError(RuntimeError):
    """Cookie transfer failed — the lane must not be treated as usable."""


def _browser_ws_url(port: int) -> str:
    endpoint = f"http://127.0.0.1:{port}/json/version"
    try:
        with urllib.request.urlopen(endpoint, timeout=_RPC_TIMEOUT_S) as resp:
            payload = json.loads(resp.read())
    except Exception as exc:
        raise CookieSeedError(f"CDP :{port} unreachable: {exc}") from exc
    ws_url = payload.get("webSocketDebuggerUrl")
    if not ws_url:
        raise CookieSeedError(f"CDP :{port} exposed no browser websocket")
    return str(ws_url)


def _cdp_call(port: int, method: str, params: dict | None = None) -> dict:
    """One browser-level CDP round trip; raises ``CookieSeedError`` on failure."""
    ws_url = _browser_ws_url(port)
    try:
        conn = websocket.create_connection(
            ws_url,
            timeout=_RPC_TIMEOUT_S,
            header=[f"Origin: http://127.0.0.1:{port}"],
        )
    except Exception as exc:
        raise CookieSeedError(f"CDP :{port} websocket refused: {exc}") from exc
    try:
        conn.send(json.dumps({"id": 1, "method": method, "params": params or {}}))
        while True:
            message = json.loads(conn.recv())
            if message.get("id") == 1:
                break
    except Exception as exc:
        raise CookieSeedError(f"CDP :{port} {method} failed: {exc}") from exc
    finally:
        conn.close()
    if "error" in message:
        raise CookieSeedError(f"CDP :{port} {method}: {message['error']}")
    return message.get("result") or {}


def _to_cookie_param(cookie: dict) -> dict:
    param = {k: cookie[k] for k in _COOKIE_PARAM_FIELDS if k in cookie}
    # A session cookie carries expires=-1; sending that back pins it to the epoch.
    if cookie.get("session"):
        param.pop("expires", None)
    return param


def read_session_cookies(
    port: int = PRIMARY_CDP_PORT,
    *,
    domain_suffix: str = COOKIE_DOMAIN_SUFFIX,
) -> list[dict]:
    """Cookies for ``domain_suffix`` held by the browser on ``port``."""
    result = _cdp_call(port, "Storage.getCookies")
    cookies = [
        _to_cookie_param(c)
        for c in result.get("cookies") or []
        if str(c.get("domain") or "").lstrip(".").endswith(domain_suffix)
    ]
    if not cookies:
        raise CookieSeedError(
            f"CDP :{port} holds no {domain_suffix} cookies — "
            "the source browser is signed out"
        )
    return cookies


def seed_lane_cookies(
    port: int,
    *,
    source_port: int = PRIMARY_CDP_PORT,
    domain_suffix: str = COOKIE_DOMAIN_SUFFIX,
) -> int:
    """Copy ``domain_suffix`` cookies from ``source_port`` into the lane on ``port``.

    Returns the number of cookies installed. Raises ``CookieSeedError`` when the
    source is unreachable or signed out — the caller must tear the lane down
    rather than hand back a browser that will fail at ``ensure_cowork_auto``.
    """
    if port == source_port:
        raise CookieSeedError(f"refusing to seed :{port} from itself")
    cookies = read_session_cookies(source_port, domain_suffix=domain_suffix)
    _cdp_call(port, "Storage.setCookies", {"cookies": cookies})
    return len(cookies)

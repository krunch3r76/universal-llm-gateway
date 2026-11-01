"""Surface-specific read-only probe JS (ChatGPT first)."""

from __future__ import annotations

from urllib.parse import urlsplit

CHATGPT_PROBE_JS = """
() => {
  const url = location.href;
  const composer = document.querySelector("#prompt-textarea, textarea[data-id], textarea");
  const loginWall = /auth\\.openai\\.com/i.test(url)
    || !!document.querySelector('button[data-testid="login-button"]');
  return {
    url,
    title: document.title,
    login_wall: loginWall,
    signed_in: loginWall ? false : (composer ? true : null),
    composer: !!composer,
  };
}
"""

_SUPPORTED_SURFACES = frozenset({"chatgpt", "openai", ""})
_CHATGPT_HOSTS = frozenset({"chatgpt.com", "www.chatgpt.com"})


def signed_in_from_signals(*, login_wall: bool, composer: bool) -> bool | None:
    """Tri-state sign-in: wall is false, composer is true, neither is unknown."""
    if login_wall:
        return False
    if composer:
        return True
    return None


def canonical_page_url(url: str) -> str:
    """Normalize a page URL for equality: host case, no fragment, no trailing slash.

    Query strings are kept so two tabs that differ only by query stay distinct
    when their paths match.
    """
    parts = urlsplit((url or "").strip())
    host = (parts.hostname or "").lower()
    port = f":{parts.port}" if parts.port else ""
    path = parts.path.rstrip("/") or "/"
    return f"{(parts.scheme or '').lower()}://{host}{port}{path}" + (
        f"?{parts.query}" if parts.query else ""
    )


def url_in_surface_scope(surface: str, url: str) -> bool:
    """Host and path gate for *surface* before any DOM evaluation.

    ChatGPT observation may run on ``chatgpt.com`` paths ``/``, ``/c/…``, and
    ``/g/…`` only. Other hosts (including auth and account sites) are refused.
    """
    if surface not in _SUPPORTED_SURFACES:
        return False
    parts = urlsplit((url or "").strip())
    host = (parts.hostname or "").lower()
    if host not in _CHATGPT_HOSTS:
        return False
    path = parts.path or "/"
    if path == "/":
        return True
    trimmed = path.rstrip("/")
    return (
        trimmed == "/c"
        or trimmed == "/g"
        or path.startswith("/c/")
        or path.startswith("/g/")
    )


def probe_js_for_surface(surface: str) -> str:
    """Return evaluate expression for *surface* (default chatgpt)."""
    if surface in _SUPPORTED_SURFACES:
        return CHATGPT_PROBE_JS
    raise ValueError(f"unsupported_surface:{surface}")


def unsupported_surface_error(surface: str) -> dict[str, object]:
    """Structured client error for unknown surfaces (handler maps to 200+json)."""
    return {
        "ok": False,
        "error": {"code": "unsupported_surface", "surface": surface},
    }

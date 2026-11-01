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

CLAUDE_PROBE_JS = """
() => {
  const url = location.href;
  const loginWall = /\\/login/i.test(location.pathname)
    || !!document.querySelector('a[href*="/login"], button[data-testid="login"]');
  return {
    url,
    title: document.title,
    login_wall: loginWall,
    signed_in: loginWall ? false : null,
  };
}
"""

_CHATGPT_SURFACES = frozenset({"chatgpt", "openai", ""})
_CLAUDE_SURFACES = frozenset({"claude", "claude-console"})
_SUPPORTED_SURFACES = _CHATGPT_SURFACES | _CLAUDE_SURFACES
_CHATGPT_HOSTS = frozenset({"chatgpt.com", "www.chatgpt.com"})
_CLAUDE_HOSTS = frozenset({"claude.ai", "www.claude.ai"})
_CLAUDE_CONSOLE_HOSTS = frozenset(
    {"console.anthropic.com", "platform.claude.com", "www.platform.claude.com"}
)


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


def _url_https_default_port(parts) -> bool:
    """Refuse cleartext and non-default HTTPS ports before host matching."""
    if (parts.scheme or "").lower() != "https":
        return False
    port = parts.port
    return port is None or port == 443


def _chatgpt_url_in_scope(url: str) -> bool:
    parts = urlsplit((url or "").strip())
    if not _url_https_default_port(parts):
        return False
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


def url_in_surface_scope(surface: str, url: str) -> bool:
    """Host and path gate for *surface* before any DOM evaluation.

    ChatGPT observation may run on ``chatgpt.com`` paths ``/``, ``/c/…``, and
    ``/g/…`` only. Claude surfaces allow any path on their hosts.
    """
    if surface not in _SUPPORTED_SURFACES:
        return False
    parts = urlsplit((url or "").strip())
    if not _url_https_default_port(parts):
        return False
    if surface in _CHATGPT_SURFACES:
        return _chatgpt_url_in_scope(url)
    host = (parts.hostname or "").lower()
    if surface == "claude":
        return host in _CLAUDE_HOSTS
    if surface == "claude-console":
        return host in _CLAUDE_CONSOLE_HOSTS
    return False


def probe_js_for_surface(surface: str) -> str:
    """Return evaluate expression for *surface* (default chatgpt)."""
    if surface in _CHATGPT_SURFACES:
        return CHATGPT_PROBE_JS
    if surface in _CLAUDE_SURFACES:
        return CLAUDE_PROBE_JS
    raise ValueError(f"unsupported_surface:{surface}")


def unsupported_surface_error(surface: str) -> dict[str, object]:
    """Structured client error for unknown surfaces (handler maps to 200+json)."""
    return {
        "ok": False,
        "error": {"code": "unsupported_surface", "surface": surface},
    }

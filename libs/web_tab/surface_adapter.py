"""Surface-specific read-only probe JS (ChatGPT first)."""

from __future__ import annotations

from urllib.parse import SplitResult

from web_tab.config import _safe_urlsplit

CHATGPT_PROBE_JS = """
() => {
  const url = location.href;
  const composer = document.querySelector(
    "#prompt-textarea, div.ProseMirror[contenteditable='true'], "
    + "[contenteditable='true'][role='textbox'], textarea[data-id], textarea"
  );
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
_ANTHROPIC_CONSOLE_SURFACES = frozenset(
    {"anthropic_console", "claude-console", "claude"}
)
_SUPPORTED_SURFACES = _CHATGPT_SURFACES | _ANTHROPIC_CONSOLE_SURFACES
_CHATGPT_HOSTS = frozenset({"chatgpt.com", "www.chatgpt.com"})
_CLAUDE_AI_HOSTS = frozenset({"claude.ai", "www.claude.ai"})
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


def is_claude_ai_chat_url(url: str) -> bool:
    """True for claude.ai product chat hosts (owned by cdp_ask, not web_tab)."""
    parts = _safe_urlsplit((url or "").strip())
    if parts is None:
        return False
    host = (parts.hostname or "").lower()
    return host in _CLAUDE_AI_HOSTS


def surface_not_web_tab_owned_refusal(url: str) -> dict[str, object] | None:
    """Structured refusal when a URL belongs to cdp_ask rather than web_tab."""
    if not is_claude_ai_chat_url(url):
        return None
    return {
        "ok": False,
        "code": "surface_not_web_tab_owned",
        "reason": "claude.ai chat is owned by Jupiter :2 cdp_ask; use cdp_ask, not web_tab",
        "owner": "cdp_ask",
    }


def _parsed_port(parts: SplitResult) -> int | None:
    """Return URL port or None; invalid port strings do not raise."""
    try:
        return parts.port
    except ValueError:
        return None


def canonical_page_url(url: str) -> str:
    """Normalize a page URL for equality: host case, no fragment, no trailing slash.

    Query strings are kept so two tabs that differ only by query stay distinct
    when their paths match.
    """
    parts = _safe_urlsplit((url or "").strip())
    if parts is None:
        return ""
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    port = _parsed_port(parts)
    port_suffix = ""
    if port is not None and not (scheme == "https" and port == 443):
        port_suffix = f":{port}"
    path = parts.path.rstrip("/") or "/"
    return f"{scheme}://{host}{port_suffix}{path}" + (
        f"?{parts.query}" if parts.query else ""
    )


def _url_https_default_port(parts: SplitResult) -> bool:
    """Refuse cleartext and non-default HTTPS ports before host matching."""
    if (parts.scheme or "").lower() != "https":
        return False
    try:
        port = parts.port
    except ValueError:
        return False
    return port is None or port == 443


def _chatgpt_url_in_scope(url: str) -> bool:
    parts = _safe_urlsplit((url or "").strip())
    if parts is None or not _url_https_default_port(parts):
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
    ``/g/…`` only. Anthropic console surfaces allow console/platform hosts only
    (never claude.ai chat).
    """
    if surface not in _SUPPORTED_SURFACES:
        return False
    if is_claude_ai_chat_url(url):
        return False
    parts = _safe_urlsplit((url or "").strip())
    if parts is None or not _url_https_default_port(parts):
        return False
    if surface in _CHATGPT_SURFACES:
        return _chatgpt_url_in_scope(url)
    host = (parts.hostname or "").lower()
    if surface in _ANTHROPIC_CONSOLE_SURFACES:
        return host in _CLAUDE_CONSOLE_HOSTS
    return False


def probe_js_for_surface(surface: str) -> str:
    """Return evaluate expression for *surface* (default chatgpt)."""
    if surface in _CHATGPT_SURFACES:
        return CHATGPT_PROBE_JS
    if surface in _ANTHROPIC_CONSOLE_SURFACES:
        return CLAUDE_PROBE_JS
    raise ValueError(f"unsupported_surface:{surface}")


def unsupported_surface_error(surface: str) -> dict[str, object]:
    """Structured client error for unknown surfaces (handler maps to 200+json)."""
    return {
        "ok": False,
        "error": {"code": "unsupported_surface", "surface": surface},
    }

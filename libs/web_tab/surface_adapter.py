"""Surface-specific read-only probe JS (ChatGPT first)."""

from __future__ import annotations

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
    signed_in: !loginWall && !!composer && /chatgpt\\.com/i.test(url),
    composer: !!composer,
  };
}
"""

_SUPPORTED_SURFACES = frozenset({"chatgpt", "openai", ""})


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

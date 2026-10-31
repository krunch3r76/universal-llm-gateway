"""Surface-specific read-only probe JS (ChatGPT first)."""

from __future__ import annotations

CHATGPT_PROBE_JS = """
() => {
  const body = (document.body && document.body.innerText) || "";
  const url = location.href;
  const composer = document.querySelector("#prompt-textarea, textarea[data-id], textarea");
  const loginWall = /log in|sign in to continue|welcome back/i.test(body)
    || /auth\\.openai\\.com/i.test(url);
  return {
    url,
    title: document.title,
    login_wall: loginWall,
    signed_in: !loginWall && !!composer && /chatgpt\\.com/i.test(url),
    composer: !!composer,
    body_start: body.slice(0, 400),
  };
}
"""


def probe_js_for_surface(surface: str) -> str:
    """Return evaluate expression for *surface* (default chatgpt)."""
    if surface in ("chatgpt", "openai", ""):
        return CHATGPT_PROBE_JS
    raise ValueError(f"unsupported_surface:{surface}")

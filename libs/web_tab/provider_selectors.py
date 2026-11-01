"""Provider-specific DOM selectors and evaluate snippets for web-tab actuation.

Reuses chat_session / grok_session selector strings where they exist; ChatGPT
uses the Orion composer pattern from ``web_chat_relay.orion_session``.
"""

from __future__ import annotations

import json
from typing import Literal
from urllib.parse import urlparse

from web_tab.surface_adapter import canonical_page_url

ProviderSite = Literal["chatgpt", "claude", "grok", "x_grok"]

# grok_session + cursor_bridge.dom_composer_paste
GROK_COMPOSER_SELECTOR = (
    "[data-testid='chat-input'] [contenteditable='true'], "
    "[aria-label='Ask Grok anything'][contenteditable='true'], "
    '[aria-label="Prompt"][contenteditable="true"]'
)

CHATGPT_COMPOSER_SELECTOR = "#prompt-textarea, textarea[data-id], textarea"

CLAUDE_COMPOSER_SELECTOR = (
    "[contenteditable='true'][role='textbox'], "
    "[contenteditable='true'][data-testid], "
    "[contenteditable='true']"
)

_SEND_BTN_SEL = (
    'button[aria-label="Send message"]:not([disabled]),'
    'button[aria-label="Send"]:not([disabled]),'
    'button[data-testid="send-button"]:not([disabled]),'
    "[data-testid='chat-input'] button[type='submit']:not([disabled]),"
    'button[type="submit"]:not([disabled])'
)


def site_from_url(url: str) -> ProviderSite | None:
    host = (urlparse(url).hostname or "").lower()
    if host in {"chatgpt.com", "www.chatgpt.com"}:
        return "chatgpt"
    if host in {"claude.ai", "www.claude.ai"}:
        return "claude"
    if host in {"grok.com", "www.grok.com"}:
        return "grok"
    if host in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}:
        path = urlparse(url).path or ""
        if "/i/grok" in path or path.startswith("/i/grok"):
            return "x_grok"
    return None


def surface_for_site(site: ProviderSite) -> str:
    if site == "chatgpt":
        return "chatgpt"
    if site in {"grok", "x_grok"}:
        return "grok"
    return "claude"


def composer_selector_for_site(site: ProviderSite) -> str:
    if site == "chatgpt":
        return CHATGPT_COMPOSER_SELECTOR
    if site in {"grok", "x_grok"}:
        return GROK_COMPOSER_SELECTOR
    return CLAUDE_COMPOSER_SELECTOR


def verify_committed_js(*, site: ProviderSite, prompt_text: str) -> str:
    """DOM receipt probe: user turn visible and composer cleared."""
    sel = json.dumps(composer_selector_for_site(site))
    needle = json.dumps(prompt_text.strip())
    user_sel = {
        "chatgpt": "[data-message-author-role='user'], [data-testid*='user']",
        "claude": "[data-testid='user-message'], [data-is-user-message='true']",
        "grok": "[data-testid='user-message']",
        "x_grok": "[data-testid='user-message']",
    }[site]
    return f"""
(() => {{
  const composer = document.querySelector({sel});
  const draft = composer
    ? ((composer.value !== undefined ? composer.value : composer.innerText) || '').trim()
    : '';
  const nodes = [...document.querySelectorAll({json.dumps(user_sel)})];
  const texts = nodes.map((el) => (el.innerText || '').trim()).filter(Boolean);
  const hasUser = texts.some((t) => t.includes({needle}) || {needle}.includes(t));
  const composer_empty = !draft;
  let receipt = 'dom_paste';
  if (hasUser && composer_empty) receipt = 'dom_committed';
  else if (hasUser) receipt = 'dom_paste';
  return {{
    ok: true,
    receipt,
    has_user_turn: hasUser,
    composer_empty,
    draft_len: draft.length,
  }};
}})()
"""


def read_reply_js(*, site: ProviderSite) -> str:
    assistant_sel = {
        "chatgpt": "[data-message-author-role='assistant']",
        "claude": "[data-testid='assistant-message']",
        "grok": "[data-testid='assistant-message']",
        "x_grok": "[data-testid='assistant-message']",
    }[site]
    return f"""
(() => {{
  const stop = [...document.querySelectorAll('button')].some((b) => {{
    const label = ((b.innerText || '') + ' ' + (b.getAttribute('aria-label') || '')).trim();
    return /^(stop|stop generating)$/i.test(label) || /stop generating/i.test(label);
  }});
  const streaming = stop || !!document.querySelector("[aria-busy='true']");
  const nodes = [...document.querySelectorAll({json.dumps(assistant_sel)})];
  const last = nodes.length ? nodes[nodes.length - 1] : null;
  const text = last ? (last.innerText || '').trim() : '';
  return {{ ok: true, streaming, stop, text, complete: !streaming && text.length > 0 }};
}})()
"""


def send_button_click_js(*, site: ProviderSite) -> str:
    sel = json.dumps(composer_selector_for_site(site))
    return f"""
(() => {{
  const el = document.querySelector({sel});
  if (!el) return {{ ok: false, reason: 'no_composer' }};
  const btn = document.querySelector({_SEND_BTN_SEL!r});
  if (!btn) return {{ ok: false, reason: 'no_send_button' }};
  btn.click();
  return {{ ok: true, send_clicked: true }};
}})()
"""


def match_tab_by_url(tabs: list[dict[str, str]], url: str) -> dict[str, str] | None:
    want = canonical_page_url(url)
    for row in tabs:
        if canonical_page_url(str(row.get("url") or "")) == want:
            return row
    return None


__all__ = [
    "CHATGPT_COMPOSER_SELECTOR",
    "CLAUDE_COMPOSER_SELECTOR",
    "GROK_COMPOSER_SELECTOR",
    "ProviderSite",
    "composer_selector_for_site",
    "match_tab_by_url",
    "read_reply_js",
    "send_button_click_js",
    "site_from_url",
    "surface_for_site",
    "verify_committed_js",
]

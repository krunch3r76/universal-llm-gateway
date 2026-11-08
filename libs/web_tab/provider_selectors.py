"""Provider-specific DOM selectors and evaluate snippets for web-tab actuation."""

from __future__ import annotations

import json
from typing import Literal
from urllib.parse import urlparse

from web_tab.surface_adapter import canonical_page_url

ProviderSite = Literal["chatgpt", "claude", "grok", "x_grok"]

GROK_COMPOSER_SELECTORS = [
    "[data-testid='chat-input'] [contenteditable='true']",
    "[aria-label='Ask Grok anything'][contenteditable='true']",
    "div.ProseMirror[contenteditable='true']",
    '[aria-label="Prompt"][contenteditable="true"]',
]

CHATGPT_COMPOSER_SELECTORS = [
    "#prompt-textarea",
    "div.ProseMirror[contenteditable='true']",
    "[contenteditable='true'][role='textbox']",
    "[aria-label='Work with ChatGPT'][contenteditable='true']",
    "textarea[data-id]",
    "textarea",
]

CLAUDE_COMPOSER_SELECTORS = [
    "[contenteditable='true'][role='textbox']",
    "[contenteditable='true'][data-testid]",
    "[contenteditable='true']",
]

_SEND_BTN_SELECTORS = [
    'button[aria-label="Send message"]:not([disabled])',
    'button[aria-label="Send prompt"]:not([disabled])',
    'button[aria-label="Send"]:not([disabled])',
    'button[data-testid="send-button"]:not([disabled])',
    "button[data-testid='chat-submit']:not([disabled])",
    "[data-testid='chat-input'] button[type='submit']:not([disabled])",
    'button[type="submit"]:not([disabled])',
]

_USER_TURN_SELECTOR_LISTS: dict[ProviderSite, list[str]] = {
    "chatgpt": [
        "[class*='group/user-message']",
        "[data-message-author-role='user']",
        "[class*='text-user-message']",
        ".rich-text-user-turn",
        "article[data-testid^='conversation-turn'] [data-message-author-role='user']",
        "[data-testid*='user-message']",
    ],
    "claude": [
        "[data-testid='user-message']",
        "[data-is-user-message='true']",
    ],
    "grok": ["[data-testid='user-message']"],
    "x_grok": ["[data-testid='user-message']"],
}

_ASSISTANT_TURN_SELECTOR_LISTS: dict[ProviderSite, list[str]] = {
    "chatgpt": [
        "[data-message-author-role='assistant']",
        "[class*='MarkdownRoot']",
        "article[data-testid^='conversation-turn'] [data-message-author-role='assistant']",
        "[data-testid*='assistant-message']",
    ],
    "claude": ["[data-testid='assistant-message']"],
    "grok": ["[data-testid='assistant-message']"],
    "x_grok": ["[data-testid='assistant-message']"],
}

_CHATGPT_STOP_STREAMING_SELECTORS = [
    "button[data-testid='stop-streaming']",
    "button[data-testid*='stop-streaming']",
    "button[data-testid='stop-generating']",
    "button[data-testid*='stop-generating']",
]

_USER_TURN_SELECTORS: dict[ProviderSite, str] = {
    site: ", ".join(sels) for site, sels in _USER_TURN_SELECTOR_LISTS.items()
}

_ASSISTANT_TURN_SELECTORS: dict[ProviderSite, str] = {
    site: ", ".join(sels) for site, sels in _ASSISTANT_TURN_SELECTOR_LISTS.items()
}

# Legacy single-string exports (first preferred selector)
GROK_COMPOSER_SELECTOR = GROK_COMPOSER_SELECTORS[0]
CHATGPT_COMPOSER_SELECTOR = CHATGPT_COMPOSER_SELECTORS[0]
CLAUDE_COMPOSER_SELECTOR = CLAUDE_COMPOSER_SELECTORS[0]


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


def composer_selectors_for_site(site: ProviderSite) -> list[str]:
    if site == "chatgpt":
        return list(CHATGPT_COMPOSER_SELECTORS)
    if site in {"grok", "x_grok"}:
        return list(GROK_COMPOSER_SELECTORS)
    return list(CLAUDE_COMPOSER_SELECTORS)


def composer_selector_for_site(site: ProviderSite) -> str:
    """Comma-joined selectors for legacy callers; prefer ``composer_selectors_for_site``."""
    return ", ".join(composer_selectors_for_site(site))


def _turn_helpers_js(*, site: ProviderSite) -> str:
    """Shared DOM helpers: deduped user texts and assistant nodes from fallback lists."""
    user_sels = json.dumps(_USER_TURN_SELECTOR_LISTS[site])
    asst_sels = json.dumps(_ASSISTANT_TURN_SELECTOR_LISTS[site])
    stop_sels = json.dumps(_CHATGPT_STOP_STREAMING_SELECTORS)
    chatgpt = "true" if site == "chatgpt" else "false"
    return f"""
  const userSels = {user_sels};
  const asstSels = {asst_sels};
  const stopSels = {stop_sels};
  const isChatgpt = {chatgpt};
  const dedupeTexts = (sels) => {{
    const out = [];
    const seen = new Set();
    for (const sel of sels) {{
      for (const el of document.querySelectorAll(sel)) {{
        const t = (el.innerText || '').trim();
        if (!t) continue;
        const key = t;
        if (seen.has(key)) continue;
        seen.add(key);
        out.push(t);
      }}
    }}
    return out;
  }};
  const latestUserText = () => {{
    let latest = null;
    let latestText = '';
    for (const sel of userSels) {{
      for (const el of document.querySelectorAll(sel)) {{
        const t = (el.innerText || '').trim();
        if (!t) continue;
        if (!latest || (latest.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING)) {{
          latest = el;
          latestText = t;
        }}
      }}
    }}
    return latestText;
  }};
  const assistantNodes = () => {{
    const nodes = [];
    const seen = new Set();
    for (const sel of asstSels) {{
      for (const el of document.querySelectorAll(sel)) {{
        if (seen.has(el)) continue;
        seen.add(el);
        nodes.push(el);
      }}
    }}
    return nodes;
  }};
  const visible = (el) => {{
    if (!el) return false;
    const r = el.getBoundingClientRect();
    return !!(r.width || r.height) && el.offsetParent !== null;
  }};
  const streamingStop = () => {{
    if (isChatgpt) {{
      for (const sel of stopSels) {{
        for (const btn of document.querySelectorAll(sel)) {{
          if (visible(btn) && !btn.disabled) return true;
        }}
      }}
      for (const btn of document.querySelectorAll('button')) {{
        const label = (btn.getAttribute('aria-label') || '').trim();
        if (!visible(btn) || btn.disabled) continue;
        if (/stop generating|stop streaming/i.test(label)) return true;
      }}
      return false;
    }}
    return [...document.querySelectorAll('button')].some((b) => {{
      const label = ((b.innerText || '') + ' ' + (b.getAttribute('aria-label') || '')).trim();
      return /^(stop|stop generating)$/i.test(label) || /stop generating/i.test(label);
    }});
  }};
  const turnActionsComplete = (lastAsstEl) => {{
    if (!lastAsstEl) return false;
    let root = lastAsstEl.closest('article') || lastAsstEl.closest("[class*='group/']") || lastAsstEl.parentElement;
    for (let i = 0; i < 6 && root; i += 1) {{
      if (root.querySelector('.turn-action-controls')) return true;
      root = root.parentElement;
    }}
    return false;
  }};
"""


def _composer_find_js(site: ProviderSite) -> str:
    sels = json.dumps(composer_selectors_for_site(site))
    return f"""
(() => {{
  const sels = {sels};
  for (const s of sels) {{
    const el = document.querySelector(s);
    if (el) return el;
  }}
  return null;
}})()
"""


def _scoped_send_find_js(site: ProviderSite) -> str:
    composer_js = _composer_find_js(site).strip()
    btns = json.dumps(_SEND_BTN_SELECTORS)
    return f"""
(() => {{
  const composer = ({composer_js});
  const btns = {btns};
  const root = composer ? (composer.closest('form') || composer.parentElement || document) : document;
  for (const sel of btns) {{
    const btn = root.querySelector(sel);
    if (btn) return btn;
  }}
  return null;
}})()
"""


def baseline_snapshot_js(*, site: ProviderSite) -> str:
    helpers = _turn_helpers_js(site=site)
    return f"""
(() => {{
{helpers}
  const texts = dedupeTexts(userSels);
  const assistants = assistantNodes();
  const lastAsst = assistants.length ? assistants[assistants.length - 1] : null;
  return {{
    ok: true,
    user_count: texts.length,
    user_texts: texts,
    assistant_count: assistants.length,
    last_assistant_text: lastAsst ? (lastAsst.innerText || '').trim() : '',
  }};
}})()
"""


def verify_committed_js(
    *,
    site: ProviderSite,
    prompt_text: str,
    baseline_user_count: int = 0,
) -> str:
    """DOM receipt: new latest user turn exactly matches *prompt_text*, composer empty."""
    needle = json.dumps(prompt_text.strip())
    composer_js = _composer_find_js(site).strip()
    base = int(baseline_user_count)
    helpers = _turn_helpers_js(site=site)
    return f"""
(() => {{
{helpers}
  const composer = ({composer_js});
  const draft = composer
    ? ((composer.value !== undefined ? composer.value : composer.innerText) || '').trim()
    : '';
  const texts = dedupeTexts(userSels);
  const latest = latestUserText();
  const newTurn = texts.length > {base};
  const exact = latest === {needle};
  const composer_empty = !draft;
  let receipt = 'dom_paste';
  let committedId = null;
  if (isChatgpt && newTurn && exact) {{
    let hit = null;
    for (const sel of userSels) {{
      for (const el of document.querySelectorAll(sel)) {{
        const t = (el.innerText || '').trim();
        if (t !== {needle}) continue;
        if (!hit || (hit.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING)) {{
          hit = el;
        }}
      }}
    }}
    if (hit) {{
      const art = hit.closest('article[data-turn]');
      committedId = art ? String(art.getAttribute('data-turn') || '') : null;
    }}
  }}
  if (newTurn && exact && composer_empty) receipt = 'dom_committed';
  return {{
    ok: true,
    receipt,
    has_user_turn: exact && newTurn,
    composer_empty,
    draft_len: draft.length,
    latest_user: latest,
    user_count: texts.length,
    committed_message_id: committedId,
  }};
}})()
"""


def read_reply_js(
    *,
    site: ProviderSite,
    baseline_assistant_count: int = 0,
    baseline_assistant_text: str = "",
) -> str:
    base_count = int(baseline_assistant_count)
    base_text = json.dumps((baseline_assistant_text or "").strip())
    grok_site = site in {"grok", "x_grok"}
    grok_flag = "true" if grok_site else "false"
    helpers = _turn_helpers_js(site=site)
    return f"""
(() => {{
{helpers}
  const stop = streamingStop();
  const busy = !!document.querySelector("[aria-busy='true']");
  const nodes = assistantNodes();
  const last = nodes.length ? nodes[nodes.length - 1] : null;
  const rawText = last ? (last.innerText || '').trim() : '';
  const stripGrokChip = (raw) => {{
    let t = String(raw || '').trim();
    t = t.replace(/(?:Working|Worked) for \\d+s\\.?\\s*/gi, ' ').trim();
    return t;
  }};
  const text = {grok_flag} ? stripGrokChip(rawText) : rawText;
  const chipStreaming = {grok_flag} && /(?:Working|Worked) for \\d+s/i.test(rawText);
  const nodeStreaming = last && last.getAttribute('data-is-streaming') === 'true';
  const actionsReady = isChatgpt ? turnActionsComplete(last) : false;
  const streaming = stop || busy || nodeStreaming || chipStreaming;
  const newNode = nodes.length > {base_count};
  const changed = text && text !== {base_text};
  const fresh = newNode || changed;
  const complete = !streaming && fresh && text.length > 0 && (!isChatgpt || actionsReady);
  return {{
    ok: true,
    streaming,
    stop,
    text,
    raw_text: rawText,
    complete,
    assistant_count: nodes.length,
    turn_actions: actionsReady,
  }};
}})()
"""


def send_button_click_js(*, site: ProviderSite) -> str:
    composer_js = _composer_find_js(site).strip()
    send_js = _scoped_send_find_js(site).strip()
    return f"""
(() => {{
  const el = ({composer_js});
  if (!el) return {{ ok: false, reason: 'no_composer' }};
  const btn = ({send_js});
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
    "baseline_snapshot_js",
    "composer_selector_for_site",
    "composer_selectors_for_site",
    "match_tab_by_url",
    "read_reply_js",
    "send_button_click_js",
    "site_from_url",
    "surface_for_site",
    "verify_committed_js",
]

"""Visible composer discovery — one contract for paste, draft read, and submit region.

claude.ai/code keeps a hidden ``chat-input`` stub while the live composer is a
visible ProseMirror ``contenteditable[role=textbox]``. Page-level
``querySelector('[data-testid="chat-input"]')`` without a visibility gate reads
an empty stub, so ``prove_composer_submitted`` exits early and never sends.
"""

from __future__ import annotations

# Shared visibility + selector order for evaluate() scripts (friction:41186).
_FIND_VISIBLE_COMPOSER_JS = """
  const isVisible = (el) => {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    if (parseFloat(style.opacity || '1') === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const selectors = [
    '[data-testid="chat-input"]',
    '[contenteditable="true"][role="textbox"]',
    'textarea[aria-label*="Prompt" i]',
    'textarea',
    '[contenteditable="true"][data-testid]',
    '[contenteditable="true"]',
    '[role="textbox"]',
  ];
  let composer = null;
  outer: for (const sel of selectors) {
    for (const el of document.querySelectorAll(sel)) {
      if (isVisible(el)) {
        composer = el;
        break outer;
      }
    }
  }
"""

COMPOSER_DRAFT_FROM_PAGE_JS = f"""() => {{
{_FIND_VISIBLE_COMPOSER_JS}
  if (!composer) {{
    return {{ ok: false, text: '', len: 0 }};
  }}
  const t = (composer.innerText || composer.value || composer.textContent || '').trim();
  return {{ ok: true, text: t, len: t.length }};
}}"""

COMPOSER_DRAFT_FROM_ELEMENT_JS = """(el) => {
  if (!el) return { ok: false, text: '', len: 0 };
  const t = (el.innerText || el.value || el.textContent || '').trim();
  return { ok: true, text: t, len: t.length };
}"""

COLLECT_COMPOSER_SUBMIT_JS = f"""() => {{
{_FIND_VISIBLE_COMPOSER_JS}
  if (!composer) {{
    return {{ ok: false, step: 'composer_missing', candidates: [] }};
  }}
  const rect = composer.getBoundingClientRect();
  const pad = 140;
  const region = {{
    left: rect.left - 24,
    top: rect.top - 24,
    right: rect.right + pad,
    bottom: rect.bottom + pad,
  }};
  const inRegion = (el) => {{
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const cx = r.left + r.width / 2;
    const cy = r.top + r.height / 2;
    return cx >= region.left && cx <= region.right
      && cy >= region.top && cy <= region.bottom;
  }};
  const candidates = [];
  for (const btn of document.querySelectorAll('button, [role="button"]')) {{
    if (btn.disabled || btn.getAttribute('aria-disabled') === 'true') continue;
    const style = window.getComputedStyle(btn);
    if (style.display === 'none' || style.visibility === 'hidden') continue;
    if (parseFloat(style.opacity || '1') === 0) continue;
    if (!inRegion(btn)) continue;
    const aria = (btn.getAttribute('aria-label') || '').trim();
    const text = (btn.innerText || '').trim().replace(/\\s+/g, ' ');
    const name = aria || text;
    if (!name) continue;
    candidates.push({{
      aria,
      text,
      name,
      role: btn.getAttribute('role') || 'button',
    }});
  }}
  return {{ ok: true, step: 'collected', candidates }};
}}"""

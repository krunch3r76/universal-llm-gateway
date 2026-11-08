"""Shared ProseMirror structural-empty core for tab-sync and chatgpt_work preflight."""

# Single TreeWalker + block/BR rules; callers add attachment/atom/draft guards.
STRUCTURAL_COMPOSER_EMPTY_CORE_JS = """
const __ulgStructuralComposerEmptyCore = (el) => {
  let visibleText = '';
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    const t = node.textContent || '';
    if (node.parentNode === el && /^[\\n\\r\\t\\x20]*$/.test(t)) continue;
    visibleText += t;
  }
  if (/[\\u00a0\\u200b\\u200c\\u200d\\ufeff\\t]/.test(visibleText)) return false;
  if (visibleText.includes(' ')) return false;
  if (/[^\\s\\n\\r]/.test(visibleText)) return false;
  const blocks = Array.from(el.children).filter((c) => {
    const tg = c.tagName;
    return tg === 'P' || tg === 'DIV' || /^H[1-6]$/.test(tg);
  });
  if (blocks.length !== 1) return false;
  const block = blocks[0];
  const elementKids = Array.from(block.childNodes).filter((n) => n.nodeType === 1);
  const textKids = Array.from(block.childNodes).filter(
    (n) => n.nodeType === 3 && (n.textContent || '').replace(/[\\s\\n\\r]/g, '').length > 0
  );
  if (textKids.length > 0) return false;
  if (elementKids.length === 0) return true;
  if (elementKids.length === 1 && elementKids[0].tagName === 'BR') return true;
  return false;
};
"""

STRUCTURAL_FOREIGN_EMBED_GUARD_JS = """
const __ulgContenteditableIsFalse = (node) => {
  if (!node || node.nodeType !== 1) return false;
  const raw = node.getAttribute && node.getAttribute('contenteditable');
  if (raw == null) return false;
  return String(raw).trim().toLowerCase() === 'false';
};
const __ulgHasNoneditableDescendant = (el) => {
  const walk = (node) => {
    if (!node || node.nodeType !== 1) return false;
    if (__ulgContenteditableIsFalse(node)) return true;
    for (const c of node.children || []) {
      if (walk(c)) return true;
    }
    return false;
  };
  return walk(el);
};
const __ulgHasForeignStructuralEmbed = (el) => {
  if (!el || typeof el.querySelector !== 'function') return false;
  if (el.querySelector('atom,img,svg,a[href]')) return true;
  return __ulgHasNoneditableDescendant(el);
};
"""

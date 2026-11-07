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

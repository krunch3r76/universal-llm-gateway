function assistantSourceText(root) {
  if (!root) return '';
  const clone = root.cloneNode(true);
  const nodes = [...clone.querySelectorAll('.katex')].reverse();
  for (const node of nodes) {
    if (node.querySelector('.katex')) continue;
    const ann = node.querySelector('annotation[encoding="application/x-tex"]');
    let tex = ann ? String(ann.textContent || '').trim() : '';
    if (!tex) {
      const visual = node.querySelector('.katex-html');
      tex = String((visual && visual.innerText) || node.innerText || '').replace(/\s+/g, '');
    }
    const display = !!(
      node.parentElement && node.parentElement.classList.contains('katex-display')
    );
    const wrapped = display ? ('$$' + tex + '$$') : ('$' + tex + '$');
    node.replaceWith(document.createTextNode(wrapped));
  }
  return String(clone.innerText || '').trim();
}

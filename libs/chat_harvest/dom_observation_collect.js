function (cfg) {
  const terms = ((cfg && cfg.match_terms) || []).map(function (t) {
    return String(t || '').toLowerCase();
  }).filter(Boolean);
  const maxNodes = Math.min(Math.max((cfg && cfg.max_nodes) || 20, 1), 50);
  const maxInsideMessage = Math.min(5, maxNodes);
  const messageSel = (cfg && cfg.message_sel) || [];
  const composerSel = (cfg && cfg.composer_sel) || [];
  const excludedParts = (cfg && cfg.excluded_parts) || [];
  const caps = (cfg && cfg.caps) || {};
  const textCap = caps.text_chars || 500;
  const ctrlCap = caps.controls_per_node || 8;
  const ctrlTextCap = caps.control_text || 80;
  const ariaCap = caps.aria_attrs || 12;
  const ariaValCap = caps.aria_val || 120;
  const classCap = caps.class_prefix || 120;
  const pathCap = caps.path_entries || 8;
  const pathPartCap = caps.path_part || 80;
  const scanCap = caps.text_nodes_scanned || 50000;
  const root = document.querySelector('main') || document.body;
  if (!root) {
    return { nodes: [], counts: { outside: 0, inside_message: 0, inside_composer: 0, excluded_other: 0, total: 0 }, scan_capped: false, page: {} };
  }
  function clip(s, n) {
    s = String(s || '');
    return s.length > n ? s.slice(0, n) : s;
  }
  function textInfo(raw) {
    const s = String(raw || '');
    const chars = s.length;
    const cut = chars > textCap;
    return { page_text: clip(s, textCap), chars: chars, cut: cut };
  }
  function closestMatch(el, selectors) {
    for (const sel of selectors) {
      try {
        if (el.closest(sel)) return sel;
      } catch (e) { /* invalid selector */ }
    }
    return null;
  }
  function classify(el) {
    if (closestMatch(el, messageSel)) return { region: 'inside_message', excluded_by: null };
    if (closestMatch(el, composerSel)) return { region: 'inside_composer', excluded_by: null };
    for (const part of excludedParts) {
      try {
        if (el.closest(part)) return { region: 'excluded_other', excluded_by: part };
      } catch (e) { /* */ }
    }
    return { region: 'outside', excluded_by: null };
  }
  function describeControls(container) {
    const out = [];
    const els = container.querySelectorAll("button, a[href], [role='button']");
    for (let i = 0; i < els.length && out.length < ctrlCap; i += 1) {
      const el = els[i];
      const disabled = !!el.disabled;
      const label = el.getAttribute('aria-label') || null;
      out.push({
        tag: (el.tagName || '').toLowerCase(),
        role: el.getAttribute('role'),
        text: textInfo(clip((el.innerText || ''), ctrlTextCap)),
        aria_label: label ? clip(label, ariaValCap) : null,
        disabled: disabled
      });
    }
    return out;
  }
  function ariaMap(el) {
    const out = {};
    let n = 0;
    for (const attr of el.attributes || []) {
      if (n >= ariaCap) break;
      if (attr.name && attr.name.indexOf('aria-') === 0) {
        out[attr.name] = clip(attr.value || '', ariaValCap);
        n += 1;
      }
    }
    return out;
  }
  function buildPath(el) {
    const parts = [];
    let cur = el;
    while (cur && cur !== root && parts.length < pathCap) {
      let tag = (cur.tagName || '').toLowerCase();
      const testid = cur.getAttribute('data-testid');
      if (testid) tag += "[data-testid=" + testid + "]";
      parts.unshift(clip(tag, pathPartCap));
      cur = cur.parentElement;
    }
    const rootTag = (root.tagName || 'main').toLowerCase();
    if (parts.length === 0 || parts[0] !== rootTag) parts.unshift(rootTag);
    return parts.slice(0, pathCap);
  }
  function describe(el, idx, region, excluded_by) {
    const rect = el.getBoundingClientRect();
    const text = textInfo(el.innerText || '');
    return {
      index: idx,
      region: region,
      excluded_by: excluded_by,
      tag: (el.tagName || '').toLowerCase(),
      role: el.getAttribute('role'),
      data_testid: el.getAttribute('data-testid'),
      aria: ariaMap(el),
      class_prefix: clip((el.getAttribute('class') || ''), classCap),
      text: text,
      controls: describeControls(el),
      position: {
        rect: [Math.round(rect.x), Math.round(rect.y), Math.round(rect.width), Math.round(rect.height)],
        in_viewport: rect.bottom > 0 && rect.top < window.innerHeight && rect.right > 0 && rect.left < window.innerWidth,
        visible: rect.width > 0 && rect.height > 0,
        after_last_user_message: false,
        after_last_message_node: false
      },
      path: buildPath(el)
    };
  }
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
  const matches = [];
  let scanned = 0;
  let node;
  while ((node = walker.nextNode()) && scanned < scanCap) {
    scanned += 1;
    const raw = node.nodeValue || '';
    const lower = raw.toLowerCase();
    let hit = false;
    for (const t of terms) {
      if (t && lower.indexOf(t) >= 0) { hit = true; break; }
    }
    const parent = node.parentElement;
    if (!parent) continue;
    if (!hit) {
      const al = (parent.getAttribute('aria-label') || '').toLowerCase();
      const ti = (parent.getAttribute('title') || '').toLowerCase();
      for (const t of terms) {
        if (t && (al.indexOf(t) >= 0 || ti.indexOf(t) >= 0)) { hit = true; break; }
      }
    }
    if (hit) matches.push(parent);
  }
  const counts = { outside: 0, inside_message: 0, inside_composer: 0, excluded_other: 0, total: 0 };
  const nodes = [];
  let insideUsed = 0;
  for (let i = 0; i < matches.length && nodes.length < maxNodes; i += 1) {
    const el = matches[i];
    const cls = classify(el);
    counts[cls.region] = (counts[cls.region] || 0) + 1;
    counts.total += 1;
    if (cls.region === 'inside_message') {
      if (insideUsed >= maxInsideMessage) continue;
      insideUsed += 1;
    }
    nodes.push(describe(el, nodes.length, cls.region, cls.excluded_by));
  }
  const controlsOutside = [];
  const ctrlEls = root.querySelectorAll("button, a[href], [role='button']");
  for (let i = 0; i < ctrlEls.length && controlsOutside.length < 40; i += 1) {
    const el = ctrlEls[i];
    const cls = classify(el);
    if (cls.region === 'inside_message' || cls.region === 'inside_composer') continue;
    controlsOutside.push({
      tag: (el.tagName || '').toLowerCase(),
      role: el.getAttribute('role'),
      text: textInfo(clip((el.innerText || ''), 80)),
      aria_label: el.getAttribute('aria-label'),
      disabled: !!el.disabled
    });
  }
  return {
    nodes: nodes,
    counts: counts,
    scan_capped: scanned >= scanCap,
    page: {
      ready_state: document.readyState,
      login_wall: /\/login/i.test(location.href) || /\/logout/i.test(location.href),
      streaming: !!document.querySelector("[aria-busy='true']"),
      root: root.tagName ? root.tagName.toLowerCase() : 'body',
      window_scroll_y: window.scrollY || 0,
      body_scroll_height: (document.body && document.body.scrollHeight) || 0,
      inner_height: window.innerHeight || 0,
      title: textInfo(document.title || '')
    },
    controls_outside_messages: controlsOutside,
    text_nodes_scanned: scanned
  };
}
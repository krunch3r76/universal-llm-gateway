function (cfg) {
  const terms = ((cfg && cfg.match_terms) || []).map(function (t) {
    return String(t || '').toLowerCase();
  }).filter(Boolean);
  const maxNodes = Math.min(Math.max((cfg && cfg.max_nodes) || 20, 1), 50);
  const maxInsideMessage = Math.min(5, maxNodes);
  const messageSel = (cfg && cfg.message_sel) || [];
  const userMessageSel = (cfg && cfg.user_message_sel) || [];
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
    return { nodes: [], containers: [], counts: { outside: 0, inside_message: 0, inside_composer: 0, excluded_other: 0, total: 0, returned: 0 }, scan_capped: false, text_nodes_scanned: 0, page: { turn_nodes: 0, user_turn_nodes: 0 } };
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
  function controlTextInfo(raw) { const s = String(raw || ''); return { page_text: clip(s, ctrlTextCap), chars: s.length, cut: s.length > ctrlTextCap }; }
  function closestMatch(el, selectors) {
    for (const sel of selectors) {
      try {
        if (el.closest(sel)) return sel;
      } catch (e) {}
    }
    return null;
  }
  function messageRoots(selectors) {
    const out = [];
    const seen = new Set();
    for (const sel of selectors) {
      let list;
      try {
        list = root.querySelectorAll(sel);
      } catch (e) {
        continue;
      }
      for (let i = 0; i < list.length; i++) {
        const el = list[i];
        if (!seen.has(el)) {
          seen.add(el);
          out.push(el);
        }
      }
    }
    return out;
  }
  function lastInDocumentOrder(els) {
    if (!els.length) return null;
    let last = els[0];
    for (let i = 1; i < els.length; i++) {
      const el = els[i];
      const pos = last.compareDocumentPosition(el);
      if (pos & Node.DOCUMENT_POSITION_FOLLOWING) last = el;
    }
    return last;
  }
  function classify(el) {
    let excludedBy = null;
    for (const part of excludedParts) {
      try {
        if (el.closest(part)) { excludedBy = part; break; }
      } catch (e) {}
    }
    if (closestMatch(el, messageSel)) return { region: 'inside_message', excluded_by: excludedBy };
    if (closestMatch(el, composerSel)) return { region: 'inside_composer', excluded_by: excludedBy };
    if (excludedBy) return { region: 'excluded_other', excluded_by: excludedBy };
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
        text: controlTextInfo(el.innerText),
        aria_label: label ? clip(label, ariaValCap) : null,
        disabled: disabled,
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
  function isInsideUserMessage(el) {
    return !!closestMatch(el, userMessageSel);
  }
  function isInsideAnyMessage(el) {
    return !!closestMatch(el, messageSel);
  }
  function follows(ref, el, inside) {
    if (!ref) return null;
    if (inside(el)) return false;
    return !!(ref.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING);
  }
  function positionFlags(el) {
    return {
      after_last_message_node: follows(lastMessage, el, isInsideAnyMessage),
      after_last_user_message: follows(lastUser, el, isInsideUserMessage),
    };
  }
  function describe(el, idx, region, excluded_by, container_index) {
    const rect = el.getBoundingClientRect();
    const text = textInfo(el.innerText || '');
    const pos = positionFlags(el);
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
        after_last_user_message: pos.after_last_user_message,
        after_last_message_node: pos.after_last_message_node,
      },
      path: buildPath(el),
      container_index: container_index === undefined ? null : container_index,
    };
  }
  const messageEls = messageRoots(messageSel);
  const userEls = messageRoots(userMessageSel);
  const lastMessage = lastInDocumentOrder(messageEls);
  const lastUser = lastInDocumentOrder(userEls);
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
  const seenMatch = new Set();
  const matchedBy = new Map();
  const allMatches = [];
  let scanned = 0;
  let node;
  while ((node = walker.nextNode()) && scanned < scanCap) {
    scanned += 1;
    const lower = (node.nodeValue || '').toLowerCase();
    let hit = false;
    for (const t of terms) {
      if (lower.indexOf(t) >= 0) { hit = true; break; }
    }
    const parent = node.parentElement;
    if (!hit || !parent || seenMatch.has(parent)) continue;
    seenMatch.add(parent);
    matchedBy.set(parent, 'text');
    allMatches.push(parent);
  }
  const attrCap = caps.attr_elements_scanned || 20000;
  const attrEls = root.querySelectorAll('[aria-label], [title]');
  const attrScanned = Math.min(attrEls.length, attrCap);
  for (let i = 0; i < attrScanned; i += 1) {
    const el = attrEls[i];
    if (seenMatch.has(el)) continue;
    const al = (el.getAttribute('aria-label') || '').toLowerCase();
    const ti = (el.getAttribute('title') || '').toLowerCase();
    let by = null;
    for (const t of terms) {
      if (al.indexOf(t) >= 0) { by = 'aria-label'; break; }
      if (ti.indexOf(t) >= 0) { by = 'title'; break; }
    }
    if (!by) continue;
    seenMatch.add(el);
    matchedBy.set(el, by);
    allMatches.push(el);
  }
  const counts = { outside: 0, inside_message: 0, inside_composer: 0, excluded_other: 0, total: 0, returned: 0 };
  for (let i = 0; i < allMatches.length; i += 1) {
    const cls = classify(allMatches[i]);
    counts[cls.region] = (counts[cls.region] || 0) + 1;
    counts.total += 1;
  }
  const containers = [];
  const containerForElement = new Map();
  function containerIndexFor(el, region) {
    if (region === 'inside_message' || region === 'inside_composer') return null;
    let cur = el.parentElement;
    let highest = null;
    let depth = 0;
    while (cur && cur !== root && depth < 8) {
      depth += 1;
      let blocked = false;
      for (const sel of messageSel) {
        try {
          if (cur.querySelector(sel)) {
            blocked = true;
            break;
          }
        } catch (e) {}
      }
      if (blocked) break;
      highest = cur;
      cur = cur.parentElement;
    }
    if (!highest) return null;
    if (!containerForElement.has(highest)) {
      const idx = containers.length;
      containerForElement.set(highest, idx);
      const hc = classify(highest);
      containers.push(describe(highest, idx, hc.region, hc.excluded_by, null));
    }
    return containerForElement.get(highest);
  }
  const selected = [];
  let insideUsed = 0;
  for (let i = 0; i < allMatches.length; i += 1) {
    if (selected.length >= maxNodes) break;
    const el = allMatches[i];
    const cls = classify(el);
    if (cls.region === 'inside_message' && insideUsed >= maxInsideMessage) continue;
    if (cls.region === 'inside_message') insideUsed += 1;
    const cidx = containerIndexFor(el, cls.region);
    selected.push({ el: el, cls: cls, container_index: cidx });
  }
  counts.returned = selected.length;
  const nodes = [];
  for (let i = 0; i < selected.length; i += 1) {
    const item = selected[i];
    const d = describe(item.el, i, item.cls.region, item.cls.excluded_by, item.container_index);
    d.matched_by = matchedBy.get(item.el) || null;
    nodes.push(d);
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
      text: controlTextInfo(el.innerText),
      aria_label: el.getAttribute('aria-label'),
      disabled: !!el.disabled,
    });
  }
  const scanCapHit = scanned >= scanCap || attrEls.length > attrCap;
  return {
    nodes: nodes, containers: containers, counts: counts, scan_capped: scanCapHit, text_nodes_scanned: scanned,
    page: {
      ready_state: document.readyState, login_wall: /\/login/i.test(location.href) || /\/logout/i.test(location.href),
      streaming: !!document.querySelector("[aria-busy='true']"), root: root.tagName ? root.tagName.toLowerCase() : 'body',
      window_scroll_y: window.scrollY || 0, body_scroll_height: (document.body && document.body.scrollHeight) || 0,
      inner_height: window.innerHeight || 0, title: textInfo(document.title || ''), url: textInfo(location.href || ''),
      turn_nodes: messageEls.length, user_turn_nodes: userEls.length, text_nodes_scanned: scanned, scan_capped: scanCapHit,
      attr_elements_scanned: attrScanned, time_origin_ms: Math.round(performance.timeOrigin),
    },
    controls_outside_messages: controlsOutside,
  };
}
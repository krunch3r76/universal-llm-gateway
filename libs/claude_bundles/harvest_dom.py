"""CDP assistant-turn harvest evaluate script.

Lives here so ``chat_reply_wait`` stays under the SLOC add-gate. Wait polls
keep raw scrape (badge idle). Delivered bodies call ``finalize_scrape_body``.
"""

from __future__ import annotations

from chat_harvest.assistant_source_text import with_assistant_source_text

# innerText of .katex is rendered math (a:37508). assistantSourceText is
# injected by with_assistant_source_text after the opening brace.
_HARVEST_JS = """
({ minMsgChars, anchorMarker = '', priorMatches = 0, userSelectors = [] }) => {
  const url = location.href || '';
  const isCoworkCse = /\\/cowork\\/cse_/.test(url);
  const baseSelectors = [
    '[data-testid="assistant-message"]',
    '[data-testid="assistant-turn"]',
    'div[class*="font-claude"]',
  ];
  const coworkSelectors = isCoworkCse
    ? [
        '[data-testid*="assistant"]',
        '[class*="AssistantMessage"]',
        'article[class*="message"]',
        '[role="article"]',
      ]
    : [];
  const normalizeAnchorText = (text) =>
    (text || '').toLowerCase().replace(/[^\\p{L}\\p{N}]+/gu, ' ').trim();
  const excludedUserNode = (el) => {
    if (!el) return true;
    if (el.isContentEditable) return true;
    if (el.closest('[contenteditable="true"]')) return true;
    const testid = (el.getAttribute('data-testid') || '').toLowerCase();
    if (testid.includes('composer') || testid.includes('input')) return true;
    if (el.getAttribute('role') === 'textbox') return true;
    return false;
  };
  const documentOrder = (a, b) => {
    const pos = a.compareDocumentPosition(b);
    if (pos & Node.DOCUMENT_POSITION_FOLLOWING) return -1;
    if (pos & Node.DOCUMENT_POSITION_PRECEDING) return 1;
    return 0;
  };
  const pruneContained = (nodes) => {
    return nodes.filter(
      (el) => !nodes.some((other) => other !== el && other.contains(el))
    );
  };
  const markerNorm = normalizeAnchorText(anchorMarker);
  const anchoredMode = !!markerNorm;
  let anchorFound = false;
  let anchorMatches = 0;
  let foreignUserTurns = 0;
  let anchorEl = null;
  const seen = new Set();
  const msgs = [];
  const assistantEls = [];
  if (!anchoredMode) {
    for (const sel of [...baseSelectors, ...coworkSelectors]) {
      for (const el of document.querySelectorAll(sel)) {
        if (seen.has(el)) continue;
        seen.add(el);
        const t = assistantSourceText(el);
        if (/^You said:\\s*/i.test(t)) continue;
        if (t.length > minMsgChars) {
          msgs.push(t);
          assistantEls.push(el);
        }
      }
    }
  } else {
    const userCandidates = [];
    const userSeen = new Set();
    const userSels = [
      ...(userSelectors || []),
      '[role="article"]',
    ];
    for (const sel of userSels) {
      for (const el of document.querySelectorAll(sel)) {
        if (userSeen.has(el) || excludedUserNode(el)) continue;
        const t = (el.innerText || '').trim();
        if (!t) continue;
        if (sel === '[role="article"]' && !/^You said:\\s*/i.test(t)) continue;
        userSeen.add(el);
        userCandidates.push(el);
      }
    }
    const userOrdered = pruneContained(userCandidates).sort(documentOrder);
    let matchCount = 0;
    for (const el of userOrdered) {
      const t = normalizeAnchorText(el.innerText || '');
      if (markerNorm && t.includes(markerNorm)) {
        matchCount += 1;
        anchorEl = el;
      }
    }
    anchorMatches = matchCount;
    anchorFound = matchCount > priorMatches;
    if (anchorFound && anchorEl) {
      for (const el of userOrdered) {
        if (documentOrder(el, anchorEl) > 0) {
          const t = normalizeAnchorText(el.innerText || '');
          if (!markerNorm || !t.includes(markerNorm)) foreignUserTurns += 1;
        }
      }
    }
    const assistantCandidates = [];
    const asstSeen = new Set();
    for (const sel of [...baseSelectors, ...coworkSelectors]) {
      for (const el of document.querySelectorAll(sel)) {
        if (asstSeen.has(el)) continue;
        asstSeen.add(el);
        const t = assistantSourceText(el);
        if (/^You said:\\s*/i.test(t)) continue;
        if (t.length <= minMsgChars) continue;
        assistantCandidates.push(el);
      }
    }
    let windowEls = [];
    if (anchorFound && anchorEl) {
      const ordered = pruneContained(assistantCandidates).sort(documentOrder);
      windowEls = ordered.filter((el) => {
        if (documentOrder(el, anchorEl) <= 0) return false;
        if (el.contains(anchorEl) || anchorEl.contains(el)) return false;
        return true;
      });
    }
    for (const el of windowEls) {
      msgs.push(assistantSourceText(el));
      assistantEls.push(el);
    }
  }
  const body = msgs.length ? msgs[msgs.length - 1] : '';
  const artifactCards = [];
  let lastTurnEl = assistantEls.length ? assistantEls[assistantEls.length - 1] : null;
  if (anchoredMode) {
    for (const el of document.querySelectorAll('[data-cdp-artifact-card]')) {
      if (!lastTurnEl || !lastTurnEl.contains(el)) {
        el.removeAttribute('data-cdp-artifact-card');
      }
    }
  }
  if (lastTurnEl) {
    const cardSeen = new Set();
    const CARD_KIND_RE = /\\bdocument\\s*[·•]\\s*md\\b/i;
    const cardChromeRe =
      /^(google drive|download|copy|open|more ways to open|document\\s*[·•]\\s*md)$/i;
    const pushCard = (el, title, kind) => {
      const key = title + '::' + kind;
      if (!title || cardSeen.has(key)) return;
      cardSeen.add(key);
      el.setAttribute('data-cdp-artifact-card', String(artifactCards.length));
      artifactCards.push({ title, kind });
    };
    const inlineCardTitle = (norm) => {
      const match = norm.match(CARD_KIND_RE);
      if (!match || typeof match.index !== 'number') return '';
      return norm.slice(0, match.index).trim();
    };
    for (const el of lastTurnEl.querySelectorAll(
      'button, a, [role="button"], [class*="artifact" i], [class*="Artifact" i], '
      + '[data-testid*="artifact" i], [data-testid*="document" i]'
    )) {
      const raw = (el.innerText || el.textContent || '').trim();
      if (!raw || raw.length > 500) continue;
      const norm = raw.replace(/\\s+/g, ' ').trim();
      if (!CARD_KIND_RE.test(norm) && !/\\bdocument\\b/i.test(norm)) continue;
      const lines = raw.split('\\n').map((l) => l.trim()).filter(Boolean);
      let title = '';
      let kind = 'MD';
      for (const line of lines) {
        if (CARD_KIND_RE.test(line)) {
          kind = 'MD';
          continue;
        }
        if (cardChromeRe.test(line) || /google drive/i.test(line)) continue;
        if (/^document\\b/i.test(line)) continue;
        if (!title && line.length > 1) title = line;
      }
      if (!title) title = inlineCardTitle(norm);
      if (!title) {
        title = (el.getAttribute('aria-label') || '').trim();
      }
      pushCard(el, title, kind);
    }
    for (const el of lastTurnEl.querySelectorAll('div, article, section, li')) {
      const raw = (el.innerText || '').trim();
      if (!raw || raw.length < 10 || raw.length > 800) continue;
      if (!CARD_KIND_RE.test(raw)) continue;
      const childCardHits = [...el.querySelectorAll('div, article, section')].filter(
        (c) => c !== el && CARD_KIND_RE.test(c.innerText || '')
      );
      if (childCardHits.length > 2) continue;
      const lines = raw.split('\\n').map((l) => l.trim()).filter(Boolean);
      let title = '';
      let kind = 'MD';
      for (const line of lines) {
        if (CARD_KIND_RE.test(line)) {
          kind = 'MD';
          continue;
        }
        if (cardChromeRe.test(line) || /google drive/i.test(line)) continue;
        if (/^document\\b/i.test(line)) continue;
        if (!title && line.length > 1) title = line;
      }
      if (!title) title = inlineCardTitle(raw.replace(/\\s+/g, ' ').trim());
      pushCard(el, title, kind);
    }
  }
  const streaming = !!document.querySelector(
    '[data-is-streaming="true"], [data-is-streaming=true]'
  );
  const isGenerationStopControl = (btn) => {
    const aria = (btn.getAttribute('aria-label') || '').trim();
    const text = (btn.innerText || '').trim();
    return (
      /^stop(\\s+(generating|response|generating response))?$/i.test(aria) ||
      /^stop(\\s+(generating|response|generating response))?$/i.test(text)
    );
  };
  const generationStopRoots = () => {
    const roots = [];
    const seen = new Set();
    const add = (el) => {
      if (el && !seen.has(el)) {
        seen.add(el);
        roots.push(el);
      }
    };
    add(document.querySelector('main'));
    add(document.querySelector('[role="main"]'));
    const chatInput = document.querySelector('[data-testid="chat-input"]');
    if (chatInput) {
      add(chatInput.closest('main'));
      add(chatInput.closest('form'));
      add(chatInput.closest('[class*="composer" i]'));
      add(chatInput.closest('[class*="Composer" i]'));
    }
    const msg = document.querySelector(
      '[data-testid="assistant-message"], [data-testid*="assistant"]'
    );
    if (msg) {
      add(msg.closest('main'));
      add(msg.closest('[role="main"]'));
    }
    return roots;
  };
  let stop = false;
  for (const root of generationStopRoots()) {
    for (const b of root.querySelectorAll('button,[role=button]')) {
      if (isGenerationStopControl(b)) {
        stop = true;
        break;
      }
    }
    if (stop) break;
  }
  const errorBannerRe =
    /hit a limit|hit your .+ limit|weekly limit|rate limit|something went wrong|network error|try again later|usage limit|overloaded/i;
  const isInsideComposer = (el) => {
    if (!el) return false;
    if (el.closest('[data-testid="chat-input"]')) return true;
    if (el.closest('[class*="composer" i]')) return true;
    return false;
  };
  const bannerSelectors = [
    '[role="alert"]',
    '[role="status"]',
    '[class*="toast" i]',
    '[class*="banner" i]',
    '[data-testid*="toast" i]',
    '[data-testid*="banner" i]',
    '[data-testid*="alert" i]',
    '[data-testid*="error" i]',
  ];
  const bannerSeen = new Set();
  const bannerTexts = [];
  for (const sel of bannerSelectors) {
    for (const el of document.querySelectorAll(sel)) {
      if (bannerSeen.has(el) || isInsideComposer(el)) continue;
      bannerSeen.add(el);
      const t = (el.innerText || '').trim();
      if (t) bannerTexts.push(t);
    }
  }
  const bannerScan = bannerTexts.join('\\n');
  const errorBannerMatch = bannerScan.match(errorBannerRe);
  const errorBanner = !!errorBannerMatch;
  // Context around first match for diagnostics (poll/CLI surfaces this).
  let errorBannerText = '';
  if (errorBannerMatch && typeof errorBannerMatch.index === 'number') {
    const i = errorBannerMatch.index;
    errorBannerText = bannerScan
      .slice(Math.max(0, i - 80), i + errorBannerMatch[0].length + 120)
      .replace(/\\s+/g, ' ')
      .trim();
  }
  const toolPause = !!document.querySelector(
    '[data-testid*="tool"], [data-testid*="research"], [aria-label*="Searching" i]'
  ) && streaming;
  const modelEl = document.querySelector(
    '[data-testid="model-selector-dropdown"], [data-testid*="model"]'
  );
  const modelLabel = modelEl
    ? (modelEl.getAttribute('aria-label') || modelEl.innerText || '').trim()
    : '';
  const taskMapSteps = [
    ...document.querySelectorAll(
      '[data-testid*="step" i],[class*="task" i] li,[role="listitem"]'
    ),
  ]
    .map((e) => (e.innerText || '').trim())
    .filter(Boolean)
    .slice(0, 40);
  const taskMapPresent = taskMapSteps.length > 0;
  const taskMapStepsText = taskMapSteps.join('\\n');
  const taskMapWorking =
    /working through/i.test(taskMapStepsText) ||
    !!document.querySelector(
      '[class*="spinner" i],[aria-busy="true"],[data-testid*="progress" i]'
    );
  const taskMapIdle = taskMapPresent ? !taskMapWorking : false;
  const result = {
    url,
    cowork_cse: isCoworkCse,
    body,
    body_len: body.length,
    n: msgs.length,
    streaming,
    stop,
    error_banner: errorBanner,
    error_banner_match: errorBannerMatch ? errorBannerMatch[0] : '',
    error_banner_text: errorBannerText,
    tool_pause: toolPause,
    model_label: modelLabel,
    task_map_present: taskMapPresent,
    task_map_working: taskMapWorking,
    task_map_idle: taskMapIdle,
    artifact_cards: artifactCards,
  };
  if (anchoredMode) {
    result.anchored = true;
    result.anchor_found = anchorFound;
    result.anchor_matches = anchorMatches;
    result.foreign_user_turns = foreignUserTurns;
  }
  return result;
}
"""

HARVEST_JS = with_assistant_source_text(_HARVEST_JS)

async ({ limit, afterTurn }) => {
  const url = location.href || '';
  const title = document.title || '';
  const streaming = !!document.querySelector(
    'button[aria-label*="Stop" i], button[data-testid*="stop" i]'
  );
  const toolPause = !!document.querySelector('[data-testid*="tool" i][class*="pause" i]');
  const stop = streaming;
  const spinner = !!document.querySelector(
    '[class*="spinner" i], [class*="loading" i], svg[class*="animate" i]'
  );
  const ariaBusy = !!document.querySelector('[aria-busy="true"]');

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function authorFor(el) {
    const testid = (el.getAttribute('data-testid') || '').toLowerCase();
    if (testid === 'user-message' || testid === 'human-turn') return 'user';
    const cls = String(el.className || '');
    if (cls.includes('font-user')) return 'user';
    return 'assistant';
  }

  function excluded(el) {
    if (!el) return true;
    if (el.isContentEditable) return true;
    if (el.closest('[contenteditable="true"]')) return true;
    const testid = (el.getAttribute('data-testid') || '').toLowerCase();
    if (testid.includes('composer') || testid.includes('input')) return true;
    if (el.getAttribute('role') === 'textbox') return true;
    return false;
  }

  function findMainScroller() {
    let scroller = null;
    let maxH = 0;
    for (const el of document.querySelectorAll('*')) {
      const style = window.getComputedStyle(el);
      const oy = style.overflowY;
      if ((oy === 'auto' || oy === 'scroll') && el.scrollHeight > el.clientHeight + 100) {
        if (el.scrollHeight > maxH) {
          maxH = el.scrollHeight;
          scroller = el;
        }
      }
    }
    return scroller;
  }

  const isClaudeChat = /\/chat\//.test(url) && !/\/cowork\/cse_/.test(url);
  const loadBtnRe = /load\s+(later|earlier|previous|more)\s+messages/i;
  // Only a button the user could actually press counts. claude.ai keeps a
  // permanent screen-reader-only "Load later messages" button in the DOM
  // (class sr-only, 1x1 px, clip-path inset(50%)); matching it made the sweep
  // click forever and never converge, so coverage could never read 'full'.
  function pressable(el) {
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') return false;
    if (el.getClientRects().length === 0) return false;
    const box = el.getBoundingClientRect();
    if (box.width <= 1 || box.height <= 1) return false;
    const cs = window.getComputedStyle(el);
    if (cs.clipPath !== 'none') return false;
    if (cs.clip !== 'auto' && cs.clip !== '') return false;
    return true;
  }
  function pickLoadButton() {
    const buttons = [...document.querySelectorAll('button')]
      .filter((el) => pressable(el) && loadBtnRe.test((el.innerText || '').trim()));
    return buttons.find((el) => /earlier|previous/i.test((el.innerText || '').trim()))
      || buttons.find((el) => /later|more/i.test((el.innerText || '').trim()))
      || buttons[0]
      || null;
  }
  function countHarvestRows() {
    const transcriptRows = document.querySelectorAll('[data-testid="transcript-row"]').length;
    if (transcriptRows > 0) return transcriptRows;
    return document.querySelectorAll(
      '[data-testid="user-message"], [data-testid="human-turn"], div[class*="font-user"], '
      + '[data-testid="assistant-message"], [data-testid="assistant-turn"], div[class*="font-claude"]'
    ).length;
  }
  function extractTurnFromRow(row) {
    let author = null;
    let bodyEl = null;
    const perfRow = (row.getAttribute('data-perf-row') || '').toLowerCase();
    if (perfRow === 'human' || row.querySelector('[data-testid="user-message"]')) {
      author = 'user';
      bodyEl = row.querySelector('[data-testid="user-message"]') || row;
    } else {
      bodyEl = row.querySelector('div.font-claude-response, div[class*="font-claude"]');
      if (bodyEl) author = 'assistant';
      if (!bodyEl && perfRow === 'assistant') {
        bodyEl = row;
        author = 'assistant';
      }
    }
    if (!author || !bodyEl) return null;
    const text = (bodyEl.innerText || '').trim();
    if (/^You said:\s*/i.test(text)) return null;
    if (text.length < 1) return null;
    const timeEl = row.querySelector('time[datetime]');
    const timestamp = timeEl ? timeEl.getAttribute('datetime') : null;
    // Virtualized claude.ai rows carry their absolute conversation ordinal;
    // almost no row carries a <time datetime>, so this is the order signal.
    const rawIndex = row.getAttribute('data-index') ?? row.getAttribute('data-rs-index');
    const index = rawIndex !== null && /^\d+$/.test(rawIndex) ? Number(rawIndex) : null;
    return { author, timestamp, text, index };
  }
  let preloadClicks = 0;
  if (isClaudeChat) {
    let prevRows = -1;
    let stableRounds = 0;
    for (let round = 0; round < 30; round++) {
      const btn = pickLoadButton();
      if (btn) {
        btn.click();
        preloadClicks += 1;
        stableRounds = 0;
      }
      const scroller = findMainScroller();
      if (scroller) scroller.scrollTop = 0;
      window.scrollTo(0, 0);
      await sleep(500);
      const rowCount = countHarvestRows();
      if (!btn) {
        if (rowCount === prevRows) stableRounds += 1;
        else stableRounds = 0;
      }
      prevRows = rowCount;
      if (!btn && stableRounds >= 4) break;
    }
  }

  const rows = document.querySelectorAll('[data-testid="transcript-row"]');
  if (rows.length === 0) {
    const isCoworkCse = /\/cowork\/cse_/.test(url);
    const userSelectors = [
      '[data-testid="user-message"]',
      '[data-testid="human-turn"]',
      'div[class*="font-user"]',
    ];
    const assistantSelectors = [
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

    let scrollIterations = 0;
    let stable = false;
    let stableRounds = 0;
    let prevCount = -1;
    for (let round = 0; round < 20; round++) {
      window.scrollTo(0, 0);
      await sleep(300);
      scrollIterations = round + 1;
      const count = document.querySelectorAll(
        '[data-testid="user-message"], [data-testid="assistant-message"]'
      ).length;
      if (count === prevCount) {
        stableRounds += 1;
        if (stableRounds >= 3) {
          stable = true;
          break;
        }
      } else {
        stableRounds = 0;
      }
      prevCount = count;
    }

    const seen = new Set();
    const nodes = [];
    for (const sel of [...userSelectors, ...assistantSelectors, ...coworkSelectors]) {
      for (const el of document.querySelectorAll(sel)) {
        if (seen.has(el) || excluded(el)) continue;
        seen.add(el);
        nodes.push({ el, author: authorFor(el) });
      }
    }
    nodes.sort((a, b) => {
      const pos = a.el.compareDocumentPosition(b.el);
      if (pos & Node.DOCUMENT_POSITION_FOLLOWING) return -1;
      if (pos & Node.DOCUMENT_POSITION_PRECEDING) return 1;
      return 0;
    });

    const rawTurns = [];
    let ordinal = 0;
    let firstRowAuthor = null;
    for (const { el, author } of nodes) {
      const t = (el.innerText || '').trim();
      if (/^You said:\s*/i.test(t)) continue;
      if (t.length < 1) continue;
      const timeEl = el.closest('[data-testid="transcript-row"]')
        ? el.closest('[data-testid="transcript-row"]').querySelector('time[datetime]')
        : el.querySelector('time[datetime]');
      const timestamp = timeEl ? timeEl.getAttribute('datetime') : null;
      ordinal += 1;
      if (firstRowAuthor === null) firstRowAuthor = author;
      if (afterTurn !== null && afterTurn !== undefined && ordinal <= afterTurn) continue;
      rawTurns.push({ author, timestamp, text: t, ordinal });
    }

    const turns = rawTurns.slice(-limit);
    const truncated = rawTurns.length > limit;
    const hasUser = rawTurns.some((t) => t.author === 'user');
    const atTop = window.scrollY === 0 || document.documentElement.scrollTop === 0;
    let coverageReason;
    if (!hasUser) coverageReason = 'zero_user_turns';
    else if (streaming) coverageReason = 'streaming';
    else if (ariaBusy) coverageReason = 'aria_busy';
    else if (truncated) coverageReason = 'limit_truncated';
    else coverageReason = (stable && atTop) ? 'converged' : 'not_stable';
    const coverage = coverageReason === 'converged' ? 'full' : 'tail';

    return {
      title,
      url,
      turns,
      coverage,
      coverage_reason: coverageReason,
      scroll_iterations: scrollIterations,
      first_row_author: firstRowAuthor,
      streaming,
      stop,
      tool_pause: toolPause,
      spinner,
      aria_busy: ariaBusy,
      truncated,
      zero_user_turns: !hasUser,
    };
  }

  const listEl = document.querySelector('[data-testid="transcript-list"]');
  let scroller = null;
  if (listEl && rows[0]) {
    let el = rows[0];
    while (el) {
      const style = window.getComputedStyle(el);
      const oy = style.overflowY;
      if ((oy === 'auto' || oy === 'scroll') && el.contains(listEl)) {
        scroller = el;
        break;
      }
      el = el.parentElement;
    }
  }
  if (!scroller) {
    scroller = findMainScroller();
  }

  let scrollIterations = 0;
  let stable = false;
  let savedScrollTop = 0;
  const rawTurns = [];
  let firstRowAuthor = null;
  // Sweep exit basis; null until the sweep ran. Feeds coverage_reason.
  let sweepReason = null;
  let addedLastRound = null;
  let sweepElapsedMs = null;
  let topReached = false;
  // A full harvest is a full harvest: no round cap. The wall budget is a
  // runaway guard whose trip is reported, never a silent 'full'.
  const SWEEP_BUDGET_MS = 180000;
  if (isClaudeChat && scroller) {
    savedScrollTop = scroller.scrollTop;
    const seen = new Set();
    const ordered = [];
    const sweepStartedAt = Date.now();
    for (;;) {
      scrollIterations += 1;
      let added = 0;
      const maxH = scroller.scrollHeight;
      const steps = Math.max(20, Math.ceil(maxH / 500));
      for (let i = 0; i <= steps; i++) {
        scroller.scrollTop = Math.round((i / steps) * maxH);
        await sleep(120);
        if (i === 0 && scroller.scrollTop === 0) topReached = true;
        for (const row of document.querySelectorAll('[data-testid="transcript-row"]')) {
          const item = extractTurnFromRow(row);
          if (!item) continue;
          const key = item.index !== null
            ? 'i:' + item.index
            : (item.timestamp || '') + '|' + item.author + '|' + item.text.substring(0, 160);
          if (seen.has(key)) continue;
          seen.add(key);
          item.seq = ordered.length;
          ordered.push(item);
          added += 1;
        }
      }
      addedLastRound = added;
      const btn = pickLoadButton();
      // Converged: a whole pass surfaced nothing new and nothing is left to load.
      if (!btn && added === 0) {
        stable = true;
        sweepReason = 'converged';
        break;
      }
      if (Date.now() - sweepStartedAt > SWEEP_BUDGET_MS) {
        sweepReason = btn ? 'load_button_persistent' : 'time_budget';
        break;
      }
      if (btn) {
        btn.click();
        await sleep(400);
      }
    }
    sweepElapsedMs = Date.now() - sweepStartedAt;
    // Order by the row's own conversation index. Sorting by timestamp put the
    // newest reply (the only row with a <time datetime>) at Turn 1. Without an
    // index, fall back to carried timestamp then capture sequence.
    let carriedTs = '';
    for (const item of ordered) {
      if (item.timestamp) carriedTs = item.timestamp;
      item.sortTs = carriedTs;
    }
    ordered.sort((a, b) => {
      if (a.index !== null && b.index !== null) return a.index - b.index;
      if (a.sortTs !== b.sortTs) return a.sortTs.localeCompare(b.sortTs);
      return a.seq - b.seq;
    });
    let ordinal = 0;
    for (const item of ordered) {
      ordinal += 1;
      if (firstRowAuthor === null) firstRowAuthor = item.author;
      if (afterTurn !== null && afterTurn !== undefined && ordinal <= afterTurn) continue;
      rawTurns.push({ author: item.author, timestamp: item.timestamp, text: item.text, ordinal });
    }
  } else {
    if (scroller) {
      savedScrollTop = scroller.scrollTop;
      let stableRounds = 0;
      let prevCount = -1;
      for (let round = 0; round < 20; round++) {
        scroller.scrollTop = 0;
        await sleep(300);
        scrollIterations = round + 1;
        const count = document.querySelectorAll('[data-testid="transcript-row"]').length;
        if (count === prevCount) {
          stableRounds += 1;
          if (stableRounds >= 3) {
            stable = true;
            break;
          }
        } else {
          stableRounds = 0;
        }
        prevCount = count;
      }
    }
    const allRows = Array.from(document.querySelectorAll('[data-testid="transcript-row"]'));
    let ordinal = 0;
    for (const row of allRows) {
      const item = extractTurnFromRow(row);
      if (!item) continue;
      if (firstRowAuthor === null) firstRowAuthor = item.author;
      ordinal += 1;
      if (afterTurn !== null && afterTurn !== undefined && ordinal <= afterTurn) continue;
      rawTurns.push({ author: item.author, timestamp: item.timestamp, text: item.text, ordinal });
    }
  }

  const turns = rawTurns.slice(-limit);
  const truncated = rawTurns.length > limit;
  const hasUser = rawTurns.some((t) => t.author === 'user');
  const atTop = scroller ? scroller.scrollTop === 0 : false;
  // First blocker wins; 'converged' is the only value that yields 'full'.
  let coverageReason;
  if (!hasUser) coverageReason = 'zero_user_turns';
  else if (streaming) coverageReason = 'streaming';
  else if (ariaBusy) coverageReason = 'aria_busy';
  else if (truncated) coverageReason = 'limit_truncated';
  else if (isClaudeChat && scroller) coverageReason = sweepReason || 'sweep_not_run';
  else coverageReason = (stable && atTop) ? 'converged' : 'not_stable';
  const coverage = coverageReason === 'converged' ? 'full' : 'tail';

  if (scroller) {
    scroller.scrollTop = savedScrollTop;
  }

  return {
    title,
    url,
    turns,
    coverage,
    coverage_reason: coverageReason,
    scroll_iterations: scrollIterations,
    preload_clicks: preloadClicks,
    added_last_round: addedLastRound,
    sweep_elapsed_ms: sweepElapsedMs,
    top_reached: topReached,
    first_row_author: firstRowAuthor,
    streaming,
    stop,
    tool_pause: toolPause,
    spinner,
    aria_busy: ariaBusy,
    truncated,
    zero_user_turns: !hasUser,
  };
}

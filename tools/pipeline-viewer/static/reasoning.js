// -- Reasoning Drawer -----------------------------------------------------
// Depends on: escHtml(), shortModel() from app.js
// Depends on: getVerdict(), _reasoningCtx from detail.js

let _drawerFilterRejectsOnly = false;

function openReasoningDrawer(statementId) {
  const ctx = _reasoningCtx;
  if (!ctx) return;

  const claim = ctx.claimMap[statementId];
  if (!claim) return;

  const drawer = document.getElementById('reasoning-drawer');
  if (!drawer) return;

  const isRejected = ctx.rejectedSet.has(statementId);
  const badgeClass = isRejected ? 'rejected' : 'accepted';
  const badgeLabel = isRejected ? 'Rejected' : 'Accepted';

  // Collect per-model reasoning entries
  const entries = [];

  // Authority verdicts
  for (const am of ctx.authorityModels) {
    const auth = ctx.authVerdicts[statementId];
    if (auth && auth.authority_model === am) {
      entries.push({
        model: am,
        verdict: auth.verdict,
        reasoning: auth.reasoning || '',
        isAuthority: true,
      });
    }
  }

  // Pool verdicts
  for (const m of ctx.pool) {
    if (!ctx.votedModels.has(m)) continue;
    const vr = getVerdict(ctx.verdicts[m]?.[statementId]);
    if (!vr) continue;
    entries.push({
      model: m,
      verdict: vr.v,
      reasoning: vr.r || '',
      isAuthority: false,
    });
  }

  // Sort: rejecting models first, then accepting
  entries.sort((a, b) => {
    if (a.verdict === b.verdict) return 0;
    return a.verdict ? 1 : -1;
  });

  _drawerFilterRejectsOnly = false;
  drawer.innerHTML = _renderDrawerContent(claim, badgeClass, badgeLabel, entries);
  drawer.classList.add('visible');
}

function closeReasoningDrawer() {
  const drawer = document.getElementById('reasoning-drawer');
  if (drawer) {
    drawer.classList.remove('visible');
  }
}

function toggleReasoningFilter(btn) {
  _drawerFilterRejectsOnly = !_drawerFilterRejectsOnly;
  btn.textContent = _drawerFilterRejectsOnly ? 'Show all models' : 'Show only rejections';
  btn.classList.toggle('active', _drawerFilterRejectsOnly);

  const cards = btn.closest('#reasoning-drawer').querySelectorAll('.reasoning-card');
  for (const card of cards) {
    if (_drawerFilterRejectsOnly && card.classList.contains('pass')) {
      card.style.display = 'none';
    } else {
      card.style.display = '';
    }
  }
}

function _renderDrawerContent(claim, badgeClass, badgeLabel, entries) {
  const hasReasoning = entries.some(e => e.reasoning);
  const rejectCount = entries.filter(e => !e.verdict).length;

  const cards = entries.map(e => {
    const icon = e.verdict ? '\u2713' : '\u2717';
    const cls = e.verdict ? 'pass' : 'fail';
    const authorityTag = e.isAuthority
      ? '<span class="reasoning-authority-tag">domain authority</span>'
      : '';
    const reasoningHtml = e.reasoning
      ? `<div class="reasoning-text">${escHtml(e.reasoning)}</div>`
      : '<div class="reasoning-text reasoning-empty">No reasoning provided.</div>';

    return `
      <div class="reasoning-card ${cls}">
        <div class="reasoning-card-header">
          <span class="reasoning-verdict ${cls}">${icon}</span>
          <span class="reasoning-model-name">${escHtml(shortModel(e.model))}</span>
          ${authorityTag}
        </div>
        ${reasoningHtml}
      </div>
    `;
  }).join('');

  const filterBtn = rejectCount > 0 && rejectCount < entries.length
    ? `<button class="reasoning-filter-btn" onclick="toggleReasoningFilter(this)">Show only rejections</button>`
    : '';

  return `
    <div class="reasoning-drawer-header">
      <div class="reasoning-drawer-title">
        <span class="verdict-badge ${badgeClass}">${badgeLabel}</span>
        <span class="reasoning-claim-text">${escHtml(claim.text)}</span>
      </div>
      <div class="reasoning-drawer-actions">
        ${filterBtn}
        <button class="reasoning-close-btn" onclick="closeReasoningDrawer()">\u2715</button>
      </div>
    </div>
    <div class="reasoning-cards">
      ${hasReasoning ? cards : '<div class="reasoning-empty-state">No reasoning data available. Run a new pipeline execution to capture model reasoning.</div>'}
    </div>
  `;
}

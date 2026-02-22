// -- Reasoning Drawer -----------------------------------------------------
// Depends on: escHtml(), shortModel() from app.js
// Depends on: getVerdict(), _reasoningCtx from detail.js

let _drawerFilterRejectsOnly = false;

function _modelMatches(callModel, alias) {
  if (callModel === alias) return true;
  const norm = alias.replaceAll('_', '-');
  return callModel.includes(norm);
}

function _findModelCall(calls, model, statementId, claimText) {
  for (const c of calls) {
    if (c.call_label === 'verify_batch' && _modelMatches(c.model, model)
        && (c.metadata?.claim_ids || []).includes(statementId)) {
      return c;
    }
  }
  for (const c of calls) {
    if (c.call_label === 'verify_batch' && _modelMatches(c.model, model)
        && c.user_prompt && c.user_prompt.includes(claimText)) {
      return c;
    }
  }
  return null;
}

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

  const calls = ctx.stepModelCalls || [];

  const entries = [];

  for (const am of ctx.authorityModels) {
    const auth = ctx.authVerdicts[statementId];
    if (auth && auth.authority_model === am) {
      entries.push({
        model: am,
        verdict: auth.verdict,
        reasoning: auth.reasoning || '',
        isAuthority: true,
        call: _findModelCall(calls, am, statementId, claim.text),
      });
    }
  }

  for (const m of ctx.pool) {
    if (!ctx.votedModels.has(m)) continue;
    const vr = getVerdict(ctx.verdicts[m]?.[statementId]);
    if (!vr) continue;
    entries.push({
      model: m,
      verdict: vr.v,
      reasoning: vr.r || '',
      isAuthority: false,
      call: _findModelCall(calls, m, statementId, claim.text),
    });
  }

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

function _renderPromptSection(call) {
  if (!call) return '';
  const snapBtn = call.snapshot_request_id
    ? `<button class="snapshot-btn" onclick="event.stopPropagation(); loadSnapshot('${call.snapshot_request_id}', this)">View Snapshot</button>`
    : '';
  return `
    <details class="collapsible-section reasoning-prompt-section">
      <summary class="collapsible-header">
        Verification Prompt
        <span class="reasoning-prompt-model">${escHtml(shortModel(call.model || ''))}</span>
        ${snapBtn}
      </summary>
      <div class="reasoning-prompt-body">
        ${call.response_text ? `
          <div class="output-label">Response</div>
          <div class="output-block">${escHtml(call.response_text)}</div>` : ''}
        ${call.user_prompt ? `
          <div class="output-label">User Prompt</div>
          <div class="output-block">${escHtml(call.user_prompt)}</div>` : ''}
        ${call.system_prompt ? `
          <div class="output-label">System Prompt</div>
          <div class="output-block">${escHtml(call.system_prompt)}</div>` : ''}
        <div class="snapshot-container"></div>
      </div>
    </details>
  `;
}

function _renderDrawerContent(claim, badgeClass, badgeLabel, entries) {
  const hasReasoning = entries.some(e => e.reasoning);
  const rejectCount = entries.filter(e => !e.verdict).length;

  // Pick the first available call for the shared prompt section
  const representativeCall = entries.find(e => e.call)?.call || null;
  const promptSection = _renderPromptSection(representativeCall);

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
    ${promptSection}
    <div class="reasoning-cards">
      ${hasReasoning ? cards : '<div class="reasoning-empty-state">No reasoning data available. Run a new pipeline execution to capture model reasoning.</div>'}
    </div>
  `;
}

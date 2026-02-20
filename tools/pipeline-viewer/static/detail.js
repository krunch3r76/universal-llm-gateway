// -- Detail Panel ---------------------------------------------------------
// Depends on: escHtml(), shortModel() from app.js

// Extract verdict bool + reasoning from new {v,r} shape or legacy bare bool.
function getVerdict(entry) {
  if (entry === true || entry === false) return { v: entry, r: '' };
  if (entry && typeof entry === 'object' && 'v' in entry) return entry;
  return null;
}

// Shared state for reasoning drawer — populated by renderVoteMatrix.
let _reasoningCtx = null;

function renderDetailPanel(step) {
  const el = document.getElementById('detail-panel');
  const tabs = buildTabs(step);
  const parent = parentStepLabel(step.step_id);
  const nameHtml = parent
    ? `<span class="detail-parent-label">${escHtml(parent)} /</span> ${escHtml(displayStepName(step.step_id))}`
    : escHtml(step.step_id);
  el.innerHTML = `
    <div class="detail-header">
      <h3>${nameHtml}</h3>
      <button class="close-btn" onclick="hideDetailPanel()">\u2715</button>
    </div>
    <div class="detail-body">
      <div class="detail-tabs">
        ${tabs.map((t, i) => `<button class="detail-tab ${i===0?'active':''}" onclick="switchTab(this, '${t.id}')">${t.label}</button>`).join('')}
      </div>
      ${tabs.map((t, i) => `<div class="tab-content ${i===0?'active':''}" id="tab-${t.id}">${t.html}</div>`).join('')}
    </div>
  `;
  el.classList.add('visible');
  el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function switchTab(btn, tabId) {
  btn.closest('.detail-body').querySelectorAll('.detail-tab').forEach(t => t.classList.remove('active'));
  btn.classList.add('active');
  btn.closest('.detail-body').querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.getElementById('tab-' + tabId).classList.add('active');
}

function buildTabs(step) {
  const tabs = [];

  tabs.push({ id: 'overview', label: 'Overview', html: renderOverviewTab(step) });

  if (step.json_data && step.json_data.verdicts_by_model) {
    tabs.push({ id: 'votes', label: 'Claim Votes', html: renderVoteMatrix(step) });
  }

  if (step.domain_routing) {
    tabs.push({ id: 'domains', label: 'Domain Routing', html: renderDomainRouting(step) });
  }

  if (step.json_data && step.json_data.tiebreaker_triggered) {
    tabs.push({ id: 'tiebreaker', label: 'Tiebreaker', html: renderTiebreaker(step) });
  }

  if (step.json_data && step.json_data.compound_decomposition) {
    const cd = step.json_data.compound_decomposition;
    tabs.push({
      id: 'compounds',
      label: `Compounds (${cd.decomposed_count})`,
      html: renderCompoundDecomposition(step),
    });
  }

  if (step.model_calls && step.model_calls.length) {
    const failCount = step.model_calls.filter(c => !c.success).length;
    const label = failCount
      ? `Model Calls (${step.model_calls.length}) \u2014 ${failCount} failed`
      : `Model Calls (${step.model_calls.length})`;
    tabs.push({ id: 'calls', label, html: renderModelCallsTab(step) });
  }

  if (step.iterations) {
    tabs.push({ id: 'iterations', label: `Answers (${step.iterations.length})`, html: renderIterations(step) });
  }

  if (step.request_body || step.system_prompt || step.user_prompt) {
    tabs.push({ id: 'request', label: 'Request', html: renderRequestTab(step) });
  }

  if (step.raw_output || step.json_data) {
    tabs.push({ id: 'output', label: 'Output', html: renderOutputTab(step) });
  }

  return tabs;
}

// -- Tab Renderers --------------------------------------------------------

function renderOverviewTab(step) {
  const model = step.model_ref || step.model || 'N/A';
  const latency = step.latency_ms ? `${(step.latency_ms/1000).toFixed(2)}s` : 'N/A';
  const jd = step.json_data;
  let statsHtml = '';
  if (jd && jd.stats) {
    const s = jd.stats;
    statsHtml = `
      <div class="stats-grid">
        <div class="stat-card"><div class="val">${s.total_claims}</div><div class="lbl">Claims Decomposed</div></div>
        <div class="stat-card"><div class="val" style="color:var(--green)">${s.accepted}</div><div class="lbl">Accepted</div></div>
        <div class="stat-card"><div class="val" style="color:var(--red)">${s.rejected}</div><div class="lbl">Rejected</div></div>
        <div class="stat-card"><div class="val">${s.verification_timing?.total_models || '-'}</div><div class="lbl">Verifier Models</div></div>
        <div class="stat-card"><div class="val">${(s.decompose_latency_ms/1000).toFixed(1)}s</div><div class="lbl">Decompose Time</div></div>
        <div class="stat-card"><div class="val">${((s.verification_timing?.total_latency_ms||0)/1000).toFixed(1)}s</div><div class="lbl">Verification Time</div></div>
      </div>
    `;
    const cd = jd.compound_decomposition;
    if (cd && cd.decomposed_count > 0) {
      statsHtml += `
        <div class="stats-grid" style="margin-top:8px">
          <div class="stat-card"><div class="val">${cd.decomposed_count}</div><div class="lbl">Compounds Split</div></div>
          <div class="stat-card"><div class="val">${cd.total_sub_claims}</div><div class="lbl">Sub-claims Added</div></div>
          <div class="stat-card"><div class="val">${(cd.decompose_latency_ms / 1000).toFixed(1)}s</div><div class="lbl">Compound Decomp Time</div></div>
        </div>
      `;
    }
  }
  let errorHtml = '';
  if (step.error) {
    errorHtml = `
      <div class="step-error-card">
        <div class="step-error-title">\u2717 Step Failed</div>
        <div class="step-error-message">${escHtml(step.error)}</div>
        ${step.traceback ? `
          <details class="collapsible-section">
            <summary class="collapsible-header">Traceback</summary>
            <div class="output-block step-error-traceback">${escHtml(step.traceback)}</div>
          </details>` : ''}
      </div>`;
  }
  return `
    ${errorHtml}
    <div class="stats-grid">
      <div class="stat-card"><div class="val">${escHtml(step.step_type || '')}</div><div class="lbl">Handler Type</div></div>
      <div class="stat-card"><div class="val">${escHtml(model)}</div><div class="lbl">Model</div></div>
      <div class="stat-card"><div class="val">${latency}</div><div class="lbl">Latency</div></div>
      <div class="stat-card"><div class="val">${step.tokens.total || 0}</div><div class="lbl">Total Tokens</div></div>
      <div class="stat-card"><div class="val">${step.tokens.prompt || 0}</div><div class="lbl">Prompt Tokens</div></div>
      <div class="stat-card"><div class="val">${step.tokens.completion || 0}</div><div class="lbl">Completion Tokens</div></div>
    </div>
    ${statsHtml}
  `;
}

function renderCompoundDecomposition(step) {
  const cd = step.json_data.compound_decomposition;
  let html = `
    <div class="stats-grid">
      <div class="stat-card"><div class="val">${cd.decomposed_count}</div><div class="lbl">Compounds Split</div></div>
      <div class="stat-card"><div class="val">${cd.total_sub_claims}</div><div class="lbl">Sub-claims Created</div></div>
      <div class="stat-card"><div class="val">${(cd.decompose_latency_ms / 1000).toFixed(1)}s</div><div class="lbl">Decompose Time</div></div>
    </div>
  `;
  for (const d of (cd.details || [])) {
    const subClaims = d.sub_claims || [];
    html += `
      <div class="routing-card" style="border-left:3px solid var(--orange, #e8a840); margin-top:12px">
        <div class="heading">Compound Claim</div>
        <div style="padding:6px 0;font-size:13px;color:var(--text-dim)">${escHtml(d.parent_text || '')}</div>
        <div style="padding:4px 0 2px;font-size:11px;color:var(--text-dim);text-transform:uppercase;letter-spacing:0.5px">
          Decomposed into ${subClaims.length} sub-claims
        </div>
        ${subClaims.map((sc) =>
          `<div style="padding:4px 0 4px 12px;font-size:13px;border-left:2px solid var(--border)">
            ${escHtml(typeof sc === 'object' && sc != null && 'text' in sc ? sc.text : String(sc))}
          </div>`
        ).join('')}
      </div>
    `;
  }
  return html;
}

function renderSourceAnswer(step) {
  const jd = step.json_data;
  if (!jd) return '';
  const sentences = jd.answer_sentences || [];
  if (!sentences.length) return '';

  // Collect sentence indices referenced by rejected claims
  const rejectedIndices = new Set();
  for (const claim of (jd.rejected_claims || [])) {
    for (const idx of (claim.source_sentences || [])) {
      rejectedIndices.add(idx);
    }
  }
  if (!rejectedIndices.size) return '';

  const sentenceHtml = sentences.map((s, i) => {
    const cls = rejectedIndices.has(i) ? 'sentence-rejected' : 'sentence-ok';
    return `<span class="${cls}" title="Sentence ${i}">${escHtml(s)}</span>`;
  }).join(' ');

  const originator = jd.originator ? ` (${escHtml(shortModel(jd.originator))})` : '';
  return `
    <div class="source-answer-section">
      <div class="output-label">Source Answer${originator} — <span style="color:var(--red)">${rejectedIndices.size} sentence(s) contain rejected claims</span></div>
      <div class="source-answer-text">${sentenceHtml}</div>
      <div class="source-answer-legend">
        <span class="sentence-rejected">rejected claim source</span>
        <span class="sentence-ok">clean</span>
      </div>
    </div>
  `;
}

function renderVoteMatrix(step) {
  const jd = step.json_data;
  const verdicts = jd.verdicts_by_model;
  const verifiedSet = new Set((jd.verified_facts || []).map(f => f.statement_id));
  const rejectedSet = new Set((jd.rejected_claims || []).map(f => f.statement_id));

  // Use full verifier pool when available (includes originator); fall back to voted models
  const pool = (jd.verifier_pool && jd.verifier_pool.length) ? jd.verifier_pool : Object.keys(verdicts);
  const originator = jd.originator || '';
  const votedModels = new Set(Object.keys(verdicts));
  const allClaims = [...(jd.verified_facts || []), ...(jd.rejected_claims || [])];

  // Extract authority models from domain_routing (for domain authority column)
  const authVerdicts = step.domain_routing?.authority_verdicts || {};
  const authorityModels = [];
  const authorityModelSet = new Set();
  for (const v of Object.values(authVerdicts)) {
    const am = v.authority_model;
    if (am && !authorityModelSet.has(am)) {
      authorityModelSet.add(am);
      authorityModels.push(am);
    }
  }

  // Extract domain veto specialist verdicts (v5.1+)
  const domainVetos = step.domain_routing?.domain_veto || [];
  const vetoSpecialists = [];
  const vetoSpecialistSet = new Set();
  const vetoVerdictMap = {};  // sid → bool
  for (const dv of domainVetos) {
    const sm = dv.specialist_model;
    if (sm && !vetoSpecialistSet.has(sm)) {
      vetoSpecialistSet.add(sm);
      vetoSpecialists.push({ model: sm, domain: dv.domain });
    }
    for (const [sid, verdict] of Object.entries(dv.verdicts || {})) {
      vetoVerdictMap[sid] = verdict;
    }
  }

  // Tally counts: authority vote + pool votes (domain veto excluded — shown separately)
  const tallies = {};
  for (const claim of allClaims) {
    let yes = 0, no = 0;
    // Authority vote
    const auth = authVerdicts[claim.statement_id];
    if (auth) {
      if (auth.verdict === true) yes++;
      else if (auth.verdict === false) no++;
    }
    // Pool votes
    for (const m of pool) {
      if (!votedModels.has(m)) continue;
      const vr = getVerdict(verdicts[m]?.[claim.statement_id]);
      if (vr && vr.v === true) yes++;
      else if (vr && vr.v === false) no++;
    }
    tallies[claim.statement_id] = { yes, no, total: yes + no };
  }

  // Authority column headers
  const authorityHeaders = authorityModels.map(am => {
    const label = escHtml(shortModel(am));
    return `<th class="model-col"><span style="color:var(--purple)">${label}</span><div style="font-size:9px;color:var(--purple);font-weight:400">domain</div></th>`;
  }).join('');

  // Domain veto specialist column headers (v5.1+)
  const vetoHeaders = vetoSpecialists.map(vs => {
    const label = escHtml(shortModel(vs.model));
    return `<th class="model-col"><span style="color:var(--orange, #e8a840)">${label}</span><div style="font-size:9px;color:var(--orange, #e8a840);font-weight:400">${escHtml(vs.domain)} veto</div></th>`;
  }).join('');

  // Pool column headers
  const modelHeaders = pool.map(m => {
    const isOrig = m === originator;
    const label = escHtml(shortModel(m));
    const suffix = isOrig ? '<div style="font-size:9px;color:var(--text-dim);font-weight:400">originator</div>' : '';
    return `<th class="model-col">${label}${suffix}</th>`;
  }).join('');

  // Group sub-claims under their parent for tree rendering
  const childrenOf = {};  // parent_statement_id -> [child claims]
  const parentIds = new Set();
  for (const claim of allClaims) {
    const pid = claim.parent_statement_id;
    if (pid) {
      if (!childrenOf[pid]) childrenOf[pid] = [];
      childrenOf[pid].push(claim);
      parentIds.add(pid);
    }
  }

  // Build ordered list: parent followed by its children, then standalone claims
  const orderedClaims = [];
  const rendered = new Set();
  for (const claim of allClaims) {
    if (rendered.has(claim.statement_id)) continue;
    if (claim.parent_statement_id) continue; // children rendered after parent
    orderedClaims.push({ claim, isChild: false });
    rendered.add(claim.statement_id);
    for (const child of (childrenOf[claim.statement_id] || [])) {
      orderedClaims.push({ claim: child, isChild: true });
      rendered.add(child.statement_id);
    }
  }
  // Orphan sub-claims whose parent isn't in allClaims
  for (const claim of allClaims) {
    if (!rendered.has(claim.statement_id)) {
      orderedClaims.push({ claim, isChild: true });
    }
  }

  function renderClaimRow(claim, isChild) {
    const isRejected = rejectedSet.has(claim.statement_id);
    const tally = tallies[claim.statement_id];
    const rowClass = [
      isRejected ? 'rejected' : '',
      isChild ? 'sub-claim-row' : '',
      parentIds.has(claim.statement_id) ? 'parent-claim-row' : '',
    ].filter(Boolean).join(' ');
    const verdictBadge = isRejected
      ? `<span class="verdict-badge rejected">${tally.yes}/${tally.total}</span>`
      : `<span class="verdict-badge accepted">${tally.yes}/${tally.total}</span>`;

    // Authority vote cells
    const authCells = authorityModels.map(am => {
      const auth = authVerdicts[claim.statement_id];
      if (!auth || auth.authority_model !== am) return '<td class="vote-cell">-</td>';
      if (auth.verdict === true) return '<td class="vote-cell pass">\u2713</td>';
      if (auth.verdict === false) return '<td class="vote-cell fail">\u2717</td>';
      return '<td class="vote-cell">-</td>';
    }).join('');

    // Domain veto specialist cells (v5.1+)
    const vetoCells = vetoSpecialists.map(vs => {
      const sid = claim.statement_id;
      if (!(sid in vetoVerdictMap)) return '<td class="vote-cell">-</td>';
      if (vetoVerdictMap[sid] === true) return '<td class="vote-cell pass">\u2713</td>';
      if (vetoVerdictMap[sid] === false) return '<td class="vote-cell fail">\u2717</td>';
      return '<td class="vote-cell">-</td>';
    }).join('');

    // Pool vote cells (with reasoning tooltip)
    const cells = pool.map(m => {
      if (!votedModels.has(m)) {
        return '<td class="vote-cell skipped"></td>';
      }
      const vr = getVerdict(verdicts[m]?.[claim.statement_id]);
      if (!vr) return '<td class="vote-cell">-</td>';
      const tip = vr.r ? ` title="${escHtml(vr.r)}"` : '';
      if (vr.v === true) return `<td class="vote-cell pass"${tip}>\u2713</td>`;
      if (vr.v === false) return `<td class="vote-cell fail"${tip}>\u2717</td>`;
      return '<td class="vote-cell">-</td>';
    }).join('');

    const domainBadge = claim.domain ? `<span class="domain-badge ${claim.domain}">${claim.domain}</span>` : '';
    const treePrefix = isChild ? '<span class="tree-connector">\u2514</span>' : '';
    const parentBadge = parentIds.has(claim.statement_id)
      ? `<span class="compound-badge">compound</span>`
      : '';
    const sid = claim.statement_id;
    return `<tr class="${rowClass} claim-row" data-sid="${escHtml(sid)}" onclick="openReasoningDrawer(this.dataset.sid)">
      <td class="claim-text">${treePrefix}${escHtml(claim.text)}${domainBadge}${parentBadge}</td>
      <td>${verdictBadge}</td>
      ${authCells}${vetoCells}${cells}
    </tr>`;
  }

  const rows = orderedClaims.map(({ claim, isChild }) => renderClaimRow(claim, isChild)).join('');

  const accepted = allClaims.filter(c => verifiedSet.has(c.statement_id)).length;
  const rejectedCount = allClaims.filter(c => rejectedSet.has(c.statement_id)).length;
  const activeCount = pool.filter(m => votedModels.has(m)).length;

  const sourceAnswerHtml = renderSourceAnswer(step);

  // Build claim lookup for reasoning drawer
  const claimMap = {};
  for (const c of allClaims) claimMap[c.statement_id] = c;

  _reasoningCtx = {
    verdicts, pool, votedModels, authVerdicts, authorityModels,
    claimMap, verifiedSet, rejectedSet, vetoVerdictMap, vetoSpecialists,
  };

  return `
    ${sourceAnswerHtml}
    <div style="margin-bottom:12px">
      <span style="color:var(--green);font-weight:600">${accepted} accepted</span>
      &middot;
      <span style="color:var(--red);font-weight:600">${rejectedCount} rejected</span>
      &middot;
      <span style="color:var(--text-dim)">${activeCount} verifier models</span>
      ${pool.length > activeCount ? `<span style="color:var(--text-dim)"> (${pool.length} in pool)</span>` : ''}
      ${authorityModels.length ? `<span style="color:var(--purple)"> + ${authorityModels.length} domain authority</span>` : ''}
      ${vetoSpecialists.length ? `<span style="color:var(--orange, #e8a840)"> + ${vetoSpecialists.length} domain veto</span>` : ''}
    </div>
    <div style="overflow-x:auto">
      <table class="vote-matrix">
        <thead>
          <tr>
            <th>Claim</th>
            <th>Verdict</th>
            ${authorityHeaders}${vetoHeaders}${modelHeaders}
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div id="reasoning-drawer"></div>
  `;
}

function renderDomainRouting(step) {
  const dr = step.domain_routing;
  if (!dr) return '<div style="color:var(--text-dim)">No domain routing data.</div>';

  const authority = dr.authority_verdicts || {};
  const generalIds = dr.claims_routed_to_general || [];

  const allClaims = [...(step.json_data?.verified_facts || []), ...(step.json_data?.rejected_claims || [])];
  const claimText = {};
  for (const c of allClaims) claimText[c.statement_id] = c.text;

  // Build sets for final outcome cross-referencing
  const verifiedIds = new Set((step.json_data?.verified_facts || []).map(f => f.statement_id));
  const generalSet = new Set(generalIds);

  let html = '';

  // Section 1: Authority Verdicts (with outcome sub-labels)
  const authEntries = Object.entries(authority);
  if (authEntries.length) {
    const rejectedByAuth = authEntries.filter(([, v]) => !v.verdict);
    const acceptedByAuth = authEntries.filter(([, v]) => v.verdict);

    html += `<div class="routing-card" style="border-left:3px solid var(--purple)">
      <div class="heading">Authority Verdicts <span class="domain-badge authority">${authEntries.length} claims</span></div>`;

    if (rejectedByAuth.length) {
      html += `<div style="padding:4px 0 2px;font-size:11px;color:var(--text-dim);text-transform:uppercase;letter-spacing:0.5px;margin-top:4px">Rejected by authority (final)</div>`;
      for (const [claimId, verdict] of rejectedByAuth) {
        const text = claimText[claimId] || claimId;
        html += `<div style="padding:3px 0;font-size:13px">
          <span style="color:var(--red)">\u2717</span> ${escHtml(text)}
          <span class="domain-badge ${verdict.domain || 'math'}">${verdict.domain || 'authority'}</span>
        </div>`;
      }
    }

    if (acceptedByAuth.length) {
      html += `<div style="padding:4px 0 2px;font-size:11px;color:var(--text-dim);text-transform:uppercase;letter-spacing:0.5px;margin-top:8px">Accepted by authority (sent to general for veto check)</div>`;
      for (const [claimId, verdict] of acceptedByAuth) {
        const text = claimText[claimId] || claimId;
        html += `<div style="padding:3px 0;font-size:13px">
          <span style="color:var(--green)">\u2713</span> ${escHtml(text)}
          <span class="domain-badge ${verdict.domain || 'math'}">${verdict.domain || 'authority'}</span>
        </div>`;
      }
    }

    html += '</div>';

    // Section 2: Veto Outcomes (for authority-accepted claims that went through general)
    const vetoable = acceptedByAuth.filter(([id]) => generalSet.has(id));
    if (vetoable.length) {
      const vetoed = vetoable.filter(([id]) => !verifiedIds.has(id));
      const survived = vetoable.filter(([id]) => verifiedIds.has(id));

      html += `<div class="routing-card" style="border-left:3px solid var(--orange, #e8a840)">
        <div class="heading">Veto Outcomes <span class="domain-badge authority">${vetoable.length} claims checked</span></div>`;

      if (survived.length) {
        html += `<div style="padding:4px 0 2px;font-size:11px;color:var(--text-dim);text-transform:uppercase;letter-spacing:0.5px">General accepted (no veto)</div>`;
        for (const [claimId, verdict] of survived) {
          html += `<div style="padding:3px 0;font-size:13px">
            <span style="color:var(--green)">\u2713</span> ${escHtml(claimText[claimId] || claimId)}
            <span class="domain-badge ${verdict.domain || 'math'}">${verdict.domain || 'authority'}</span>
          </div>`;
        }
      }

      if (vetoed.length) {
        html += `<div style="padding:4px 0 2px;font-size:11px;color:var(--text-dim);text-transform:uppercase;letter-spacing:0.5px;margin-top:8px">General rejected (vetoed)</div>`;
        for (const [claimId, verdict] of vetoed) {
          html += `<div style="padding:3px 0;font-size:13px">
            <span style="color:var(--red)">\u2717</span> ${escHtml(claimText[claimId] || claimId)}
            <span class="domain-badge ${verdict.domain || 'math'}">${verdict.domain || 'authority'}</span>
          </div>`;
        }
      }

      html += '</div>';
    }
  }

  // Section 3: Routed to General (non-domain claims that went straight to general)
  const directGeneral = generalIds.filter(id => !authority[id]);
  if (directGeneral.length) {
    html += `<div class="routing-card" style="border-left:3px solid var(--blue)">
      <div class="heading">Routed to General Verification <span class="domain-badge general">${directGeneral.length} claims</span></div>
      <div style="max-height:200px;overflow-y:auto">`;
    for (const id of directGeneral) {
      html += `<div style="padding:3px 0;font-size:13px">${escHtml(claimText[id] || id)}</div>`;
    }
    html += '</div></div>';
  }

  // Section 4: Domain Veto (specialist veto on non-unanimous accepted claims, v5.1+)
  const domainVetos = dr.domain_veto || [];
  for (const dv of domainVetos) {
    const vetoed = dv.vetoed_ids || [];
    const survived = dv.survived_ids || [];
    const dvVerdicts = dv.verdicts || {};
    const domainLabel = dv.domain || 'unknown';
    const specialistLabel = dv.specialist_model || 'unknown';
    const latency = dv.latency_ms ? `${(dv.latency_ms / 1000).toFixed(1)}s` : '';

    html += `<div class="routing-card" style="border-left:3px solid var(--orange, #e8a840)">
      <div class="heading">Domain Veto: ${escHtml(domainLabel)} <span class="domain-badge ${domainLabel}">${dv.candidates_checked} non-unanimous checked</span></div>
      <div style="font-size:12px;color:var(--text-dim);padding:2px 0 6px">
        Specialist: <strong>${escHtml(shortModel(specialistLabel))}</strong>${latency ? ` \u00b7 ${latency}` : ''}
      </div>`;

    if (survived.length) {
      html += `<div style="padding:4px 0 2px;font-size:11px;color:var(--text-dim);text-transform:uppercase;letter-spacing:0.5px">Confirmed by specialist (${survived.length})</div>`;
      for (const sid of survived) {
        const text = claimText[sid] || sid;
        html += `<div style="padding:3px 0;font-size:13px">
          <span style="color:var(--green)">\u2713</span> ${escHtml(text)}
        </div>`;
      }
    }

    if (vetoed.length) {
      html += `<div style="padding:4px 0 2px;font-size:11px;color:var(--text-dim);text-transform:uppercase;letter-spacing:0.5px;margin-top:8px">Rejected by specialist (${vetoed.length})</div>`;
      for (const sid of vetoed) {
        const text = claimText[sid] || sid;
        html += `<div style="padding:3px 0;font-size:13px">
          <span style="color:var(--red)">\u2717</span> ${escHtml(text)}
        </div>`;
      }
    }

    html += '</div>';
  }

  if (!authEntries.length && !generalIds.length && !domainVetos.length) {
    html += '<div style="color:var(--text-dim)">All claims routed to general verification (no domain-specific routing).</div>';
  }

  return html;
}

function renderTiebreaker(step) {
  const jd = step.json_data;
  const tb = jd.tiebreaker_triggered;
  const rejected = jd.rejected_claims || [];
  const verified = jd.verified_facts || [];

  const modelName = tb.tiebreaker_model || 'unknown';
  const borderlineCount = (tb.borderline_claim_ids || []).length;
  const totalClaims = tb.total_claims || 0;
  const mathExcluded = tb.math_excluded || 0;

  let html = `
    <div class="tiebreaker-info">
      <div class="title">Tiebreaker \u2014 Swing Vote</div>
      <div class="detail">
        Model: <strong>${escHtml(modelName)}</strong> &middot;
        ${borderlineCount} borderline claims out of ${totalClaims}
        ${mathExcluded ? ` &middot; ${mathExcluded} math-excluded` : ''}
      </div>
    </div>
  `;

  if (rejected.length) {
    html += `<div class="output-label" style="margin-top:16px">Rejected by Tiebreaker</div>`;
    html += rejected.map(c => `
      <div style="padding:8px 12px;background:var(--red-bg);border-radius:4px;margin-top:6px;font-size:13px;color:var(--red)">
        \u2717 ${escHtml(c.text)}
      </div>
    `).join('');
  }

  html += `<div class="output-label" style="margin-top:16px">Surviving Claims (${verified.length})</div>`;
  html += `<div style="max-height:300px;overflow-y:auto">`;
  html += verified.map(c => `
    <div style="padding:6px 12px;border-bottom:1px solid var(--border);font-size:13px">
      <span style="color:var(--green)">\u2713</span> ${escHtml(c.text)}
    </div>
  `).join('');
  html += `</div>`;

  return html;
}

function renderIterations(step) {
  return `<div class="iteration-cards">
    ${step.iterations.map((it, i) => `
      <div class="iteration-card">
        <div class="iteration-header" onclick="toggleIteration(this)">
          <span class="model-name">${escHtml(shortModel(it.model))}</span>
          <span class="iter-meta">${(it.latency_ms/1000).toFixed(1)}s</span>
        </div>
        <div class="iteration-body">
          <div class="output-block">${escHtml(it.output || 'No output')}</div>
        </div>
      </div>
    `).join('')}
  </div>`;
}

function toggleIteration(header) {
  const body = header.nextElementSibling;
  body.classList.toggle('expanded');
}

function renderRequestTab(step) {
  let html = '';

  // Request body (full API payload) — shown by default
  if (step.request_body) {
    html += `<div class="output-label">Request Body</div>
             <div class="output-block">${escHtml(JSON.stringify(step.request_body, null, 2))}</div>`;
  }

  // System prompt — collapsible for quick readability
  if (step.system_prompt) {
    html += `
      <details class="collapsible-section">
        <summary class="collapsible-header">System Prompt</summary>
        <div class="output-block">${escHtml(step.system_prompt)}</div>
      </details>`;
  }

  // User prompt — collapsible for quick readability
  if (step.user_prompt) {
    html += `
      <details class="collapsible-section">
        <summary class="collapsible-header">User Prompt</summary>
        <div class="output-block">${escHtml(step.user_prompt)}</div>
      </details>`;
  }

  return html || '<div style="color:var(--text-dim)">No request data captured.</div>';
}

function renderOutputTab(step) {
  let html = '';
  if (step.raw_output) {
    html += `<div class="output-label">Raw Output</div>
             <div class="output-block">${escHtml(step.raw_output)}</div>`;
  }
  if (step.json_data) {
    html += `<div class="output-label">JSON Data</div>
             <div class="output-block">${escHtml(JSON.stringify(step.json_data, null, 2))}</div>`;
  }
  return html || '<div style="color:var(--text-dim)">No output data.</div>';
}

// -- Model Calls Tab ------------------------------------------------------

function renderModelCallsTab(step) {
  const calls = step.model_calls || [];
  if (!calls.length) return '<div style="color:var(--text-dim)">No model calls recorded.</div>';

  return `<div class="model-calls-list">
    ${calls.map((call, i) => {
      const isFailed = !call.success;
      const statusIcon = isFailed ? '\u2717' : '\u2713';
      const statusCls = isFailed ? 'failed' : 'success';
      const label = call.call_label || 'call';
      const model = shortModel(call.model || '');
      const wallMs = call.latency_ms || 0;
      const inferMs = call.inference_ms || 0;
      let latency;
      if (wallMs > 0 && inferMs > 0) {
        const queueMs = wallMs - inferMs;
        latency = `${(wallMs/1000).toFixed(2)}s `
          + `<span class="timing-split">`
          + `<span class="timing-infer" title="Actual generation time">${(inferMs/1000).toFixed(2)}s infer</span>`
          + ` · `
          + `<span class="timing-queue" title="Queue wait time">${(queueMs/1000).toFixed(2)}s queue</span>`
          + `</span>`;
      } else {
        latency = wallMs ? `${(wallMs/1000).toFixed(2)}s` : '-';
      }
      const tokens = (call.prompt_tokens || 0) + (call.completion_tokens || 0);
      const tokStr = tokens ? `${tokens} tok` : '';
      const openAttr = isFailed ? 'open' : '';
      const snapBtn = call.snapshot_request_id
        ? `<button class="snapshot-btn" onclick="event.stopPropagation(); loadSnapshot('${call.snapshot_request_id}', this)">View Snapshot</button>`
        : '';

      return `
        <details class="model-call-card ${statusCls}" ${openAttr}>
          <summary class="model-call-header">
            <span class="model-call-status ${statusCls}">${statusIcon}</span>
            <span class="model-call-label">${escHtml(label)}</span>
            <span class="model-call-model">${escHtml(model)}</span>
            <span class="model-call-meta">${latency}${tokStr ? ' \u00b7 ' + tokStr : ''}</span>
            ${snapBtn}
          </summary>
          <div class="model-call-body">
            ${isFailed ? `<div class="model-call-error">${escHtml(call.error || 'Unknown error')}</div>` : ''}
            ${call.response_text ? `
              <div class="output-label">Response</div>
              <div class="output-block model-call-response">${escHtml(call.response_text.length > 2000 ? call.response_text.slice(0, 2000) + '...' : call.response_text)}</div>` : ''}
            ${call.user_prompt ? `
              <details class="collapsible-section">
                <summary class="collapsible-header">User Prompt</summary>
                <div class="output-block">${escHtml(call.user_prompt)}</div>
              </details>` : ''}
            ${call.system_prompt ? `
              <details class="collapsible-section">
                <summary class="collapsible-header">System Prompt</summary>
                <div class="output-block">${escHtml(call.system_prompt)}</div>
              </details>` : ''}
            ${call.request_body ? `
              <details class="collapsible-section">
                <summary class="collapsible-header">Request Body</summary>
                <div class="output-block">${escHtml(JSON.stringify(call.request_body, null, 2))}</div>
              </details>` : ''}
            <div class="snapshot-container" id="snap-${i}"></div>
          </div>
        </details>`;
    }).join('')}
  </div>`;
}

async function loadSnapshot(requestId, btn) {
  const container = btn.closest('.model-call-card').querySelector('.snapshot-container');
  if (container.dataset.loaded) {
    container.style.display = container.style.display === 'none' ? 'block' : 'none';
    return;
  }
  btn.textContent = 'Loading...';
  try {
    const res = await fetch(`/api/snapshots/${requestId}`);
    if (!res.ok) {
      container.innerHTML = '<div style="color:var(--text-dim);padding:8px">No snapshots found on disk.</div>';
      container.dataset.loaded = '1';
      btn.textContent = 'View Snapshot';
      return;
    }
    const data = await res.json();
    let html = '<div class="snapshot-stages">';
    const stageLabels = {
      before: 'Before (as received)',
      after: 'After (transformed)',
      response_from_gateway: 'Response from Gateway',
      response_to_client: 'Response to Client'
    };
    for (const [key, label] of Object.entries(stageLabels)) {
      if (data[key]) {
        html += `
          <details class="collapsible-section">
            <summary class="collapsible-header">${label}</summary>
            <div class="output-block">${escHtml(JSON.stringify(data[key], null, 2))}</div>
          </details>`;
      }
    }
    html += '</div>';
    container.innerHTML = html;
    container.dataset.loaded = '1';
    btn.textContent = 'View Snapshot';
  } catch (e) {
    container.innerHTML = `<div style="color:var(--red);padding:8px">Error loading snapshot: ${escHtml(e.message)}</div>`;
    btn.textContent = 'View Snapshot';
  }
}

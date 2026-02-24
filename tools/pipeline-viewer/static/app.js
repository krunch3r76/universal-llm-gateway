// -- State ----------------------------------------------------------------
let currentExecution = null;
let selectedStepIdx = null;
let pollTimer = null;
let pinnedExecId = null;  // set on first manual selection; blocks auto-select

// -- API ------------------------------------------------------------------
async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// -- Theme ----------------------------------------------------------------
function initTheme() {
  const saved = localStorage.getItem('pv-theme') || '';
  if (saved) document.documentElement.setAttribute('data-theme', saved);
  const sel = document.getElementById('theme-select');
  if (!sel) return;
  sel.value = saved;
  sel.addEventListener('change', () => {
    const theme = sel.value;
    if (theme) document.documentElement.setAttribute('data-theme', theme);
    else document.documentElement.removeAttribute('data-theme');
    localStorage.setItem('pv-theme', theme);
  });
}

// -- Init -----------------------------------------------------------------
async function init() {
  initTheme();
  const executions = await fetchJSON('/api/executions');
  renderExecList(executions);
  startExecListPolling();
}

// -- Execution List -------------------------------------------------------
function renderExecList(executions) {
  const container = document.getElementById('exec-list');
  if (!executions.length) {
    container.innerHTML = '<div class="loading">No executions found.</div>';
    return;
  }
  container.innerHTML = executions.map((ex) => {
    const liveTag = ex.is_live ? '<span class="live-badge">LIVE</span>' : '';
    const pipelineLabel = escHtml(ex.pipeline_id);
    return `
      <div class="exec-card ${ex.is_live ? 'live' : ''}"
           data-pipeline="${ex.pipeline_id}" data-exec="${ex.execution_id}"
           data-live="${ex.is_live}" onclick="loadExecution(this)">
        <div class="question">${liveTag}${escHtml(ex.question)}</div>
        <div class="meta">${ex.step_count} steps &middot; ${ex.timestamp} &middot; <span class="pipeline-label">${pipelineLabel}</span></div>
      </div>
    `;
  }).join('');

  if (pinnedExecId) {
    document.querySelector(`.exec-card[data-exec="${pinnedExecId}"]`)?.classList.add('active');
  }
}

async function loadExecution(el) {
  disconnectStream();
  document.querySelectorAll('.exec-card').forEach(c => c.classList.remove('active'));
  el.classList.add('active');

  const pid = el.dataset.pipeline;
  const eid = el.dataset.exec;
  const isLive = el.dataset.live === 'true';
  pinnedExecId = eid;

  if (isLive) {
    connectStream(pid, eid);
  } else {
    currentExecution = await fetchJSON(`/api/executions/${pid}/${eid}`);
    selectedStepIdx = null;
    renderFullExecution();
  }
}

// -- Static rendering (completed executions) ------------------------------
function renderFullExecution() {
  setLiveIndicator(false);
  document.getElementById('question-text').textContent = currentExecution.question;
  document.getElementById('question-display').style.display = 'block';
  renderSummaryBanner(currentExecution.summary);
  renderPipelineFlow(currentExecution.steps);
  renderFinalOutput(currentExecution.steps);
  hideDetailPanel();
}

// -- Live Indicator -------------------------------------------------------
function setLiveIndicator(active) {
  const el = document.getElementById('live-indicator');
  if (active) { el.classList.add('active'); }
  else { el.classList.remove('active'); }
}

// -- Summary Banner -------------------------------------------------------
function renderSummaryBanner(summary) {
  const el = document.getElementById('summary-banner');
  const wallDur = summary.wall_clock_ms != null ? (summary.wall_clock_ms / 1000).toFixed(1) : null;
  const sumDur = summary.summed_latency_ms > 0 ? (summary.summed_latency_ms / 1000).toFixed(1) : null;
  const tokK = (summary.total_tokens / 1000).toFixed(0);

  const showSummed = sumDur !== null && wallDur !== null && sumDur !== wallDur;
  const timeStat = wallDur !== null
    ? `<div class="stat">
        <span class="stat-value amber">${wallDur}s</span>
        <span class="stat-col-labels">
          <span class="stat-label">Wall Time</span>
          ${showSummed ? `<span class="stat-sub" title="Sum of individual step durations (exceeds wall time when steps run concurrently)">Σ ${sumDur}s model</span>` : ''}
        </span>
      </div>`
    : '';

  el.innerHTML = `
    <div class="stat"><span class="stat-value blue">${summary.models_used.length}</span><span class="stat-label">Models</span></div>
    <div class="stat-divider"></div>
    <div class="stat"><span class="stat-value">${summary.total_steps}</span><span class="stat-label">Steps</span></div>
    <div class="stat-divider"></div>
    <div class="stat"><span class="stat-value">${summary.total_claims_verified}</span><span class="stat-label">Claims Verified</span></div>
    <div class="stat-divider"></div>
    <div class="stat"><span class="stat-value green">${summary.total_accepted}</span><span class="stat-label">Accepted</span></div>
    <div class="stat-divider"></div>
    <div class="stat"><span class="stat-value red">${summary.total_rejected}</span><span class="stat-label">Rejected</span></div>
    <div class="stat-divider"></div>
    <div class="stat"><span class="stat-value">${tokK}K</span><span class="stat-label">Tokens</span></div>
    <div class="stat-divider"></div>
    ${timeStat}
  `;
  el.classList.add('visible');
}

// -- Pipeline Flow --------------------------------------------------------
function renderPipelineFlow(steps) {
  const el = document.getElementById('pipeline-flow');
  const phases = groupByPhase(steps);
  el.innerHTML = phases.map(phase =>
    phase.type === 'parallel'
      ? renderParallelPhase(phase, steps)
      : renderSequentialPhase(phase, steps)
  ).join('');
  el.classList.add('visible');
}

function renderSequentialPhase(phase, allSteps) {
  return `
    <div class="phase-group">
      <div class="phase-label">${escHtml(phase.name)}</div>
      <div class="steps-row">
        ${phase.steps.map((step, i) => renderStepCard(step, allSteps, i > 0, false, true)).join('')}
      </div>
    </div>`;
}

function renderParallelPhase(phase, allSteps) {
  return `
    <div class="phase-group">
      <div class="phase-label">${escHtml(phase.name)} <span class="phase-parallel-tag">\u00d7${phase.lanes.length} parallel</span></div>
      <div class="parallel-lanes" style="--lane-count:${phase.lanes.length}">
        ${phase.lanes.map(lane => {
          const laneMs = lane.steps.reduce((s, st) => s + (st.latency_ms || 0), 0);
          return `
            <div class="lane">
              <div class="lane-header">${escHtml(formatLaneId(lane.id))} <span class="lane-latency">${(laneMs / 1000).toFixed(1)}s</span></div>
              <div class="lane-steps">
                ${lane.steps.map((step, i) => renderStepCard(step, allSteps, i > 0, true, false)).join('')}
              </div>
            </div>`;
        }).join('')}
      </div>
    </div>`;
}

function renderStepCard(step, allSteps, showArrow, vertical, showParent) {
  const idx = allSteps.indexOf(step);
  const latency = step.latency_ms ? `${(step.latency_ms/1000).toFixed(1)}s` : '-';
  const tokens = step.tokens.total || 0;
  const hasFailed = !!step.error;
  const tokStr = hasFailed ? 'ERROR' : (tokens > 1000 ? `${(tokens/1000).toFixed(0)}K tok` : `${tokens} tok`);
  const model = shortModel(step.model_ref || step.model || '');
  const badge = getBadge(step);
  const statusCls = step.status === 'running' ? 'running' : (hasFailed || step.status === 'failed' ? 'failed' : '');
  const parentLabel = parentStepLabel(step.step_id);
  let nameHtml;
  if (showParent && parentLabel) {
    nameHtml = `<div class="step-parent-label">${escHtml(parentLabel)}</div><div class="step-name">${escHtml(displayStepName(step.step_id))}</div>`;
  } else if (parentLabel) {
    nameHtml = `<div class="step-name">${escHtml(displayStepName(step.step_id))}</div>`;
  } else {
    nameHtml = `<div class="step-name">${escHtml(step.step_id)}</div>`;
  }
  const arrowCls = vertical ? 'step-arrow vertical' : 'step-arrow';
  return `
    ${showArrow ? `<span class="${arrowCls}">${vertical ? '\u2193' : '\u2192'}</span>` : ''}
    <div class="step-node cat-${step.category} ${selectedStepIdx === idx ? 'selected' : ''} ${statusCls}"
         onclick="selectStep(${idx})">
      ${badge}
      ${nameHtml}
      <div class="step-meta">${model} &middot; ${latency} &middot; ${tokStr}</div>
    </div>`;
}

function formatLaneId(id) {
  const m = id.match(/^(?:verify_)?link(\d+)$/);
  if (m) return `Link ${m[1]}`;
  return id.replace(/_/g, ' ');
}

function groupByPhase(steps) {
  const raw = [];
  let current = null;
  for (const step of steps) {
    const phaseName = getPhase(step);
    if (!current || current.name !== phaseName) {
      current = { name: phaseName, steps: [] };
      raw.push(current);
    }
    current.steps.push(step);
  }
  return raw.map(phase => {
    const lanes = detectParallelLanes(phase.steps);
    if (lanes) return { name: phase.name, type: 'parallel', lanes };
    return { name: phase.name, type: 'sequential', steps: phase.steps };
  });
}

function detectParallelLanes(steps) {
  if (steps.length < 2) return null;
  const byParent = {};
  for (const step of steps) {
    if (!step.step_id.includes('__')) return null;
    const parent = step.step_id.split('__')[0];
    if (!byParent[parent]) byParent[parent] = [];
    byParent[parent].push(step);
  }
  const parents = Object.keys(byParent);
  if (parents.length < 2) return null;
  return parents.map(p => ({ id: p, steps: byParent[p] }));
}

function getPhase(step) {
  const id = step.step_id;

  // Sub-pipeline steps: group by parent prefix
  if (id.includes('__')) {
    const parent = id.split('__')[0];
    if (parent.startsWith('verify_link')) return 'Verify Chain';
    if (parent === 'veto_pass') return 'Veto Pass';
    if (parent === 'synthesize') return 'Synthesize';
    return 'Pipeline';
  }

  if (id === 'analyze_question' || id === 'classify_expansion_safety') return 'Classify';
  if (id === 'answer_all') return 'Answer (Parallel)';
  if (id === 'output_gate') return 'Output Gate';
  if (id.includes('iter2') || id === 'reseed_qwen') return 'Pass 2 \u2014 Expansion';
  if (id === 'post_process') return 'Pass 1 \u2014 Synthesize';
  if (id === 'post_process_final') return 'Pass 2 \u2014 Synthesize';
  if (['verify_link0','enrich_link1','verify_link1','enrich_link2','verify_link2','tiebreaker_pass'].includes(id)) {
    return 'Pass 1 \u2014 Verify & Enrich Chain';
  }
  // v6.0 non-sub-pipeline steps
  if (id === 'synergize' || id === 'filter_negatives') return 'Merge & Filter';
  if (id === 'veto_pass') return 'Veto Pass';
  return 'Pipeline';
}

// Format sub-pipeline step IDs for display: "verify_link0__decompose" → "decompose"
// with a small parent label rendered separately.
function displayStepName(stepId) {
  if (!stepId.includes('__')) return stepId;
  return stepId.split('__')[1];
}

function parentStepLabel(stepId) {
  if (!stepId.includes('__')) return '';
  return stepId.split('__')[0];
}

function getBadge(step) {
  const jd = step.json_data;
  if (jd && jd.stats) {
    const rej = jd.stats.rejected || 0;
    if (rej > 0) return `<span class="step-badge" style="background:var(--red-bg);color:var(--red)">-${rej}</span>`;
    return `<span class="step-badge" style="background:var(--green-bg);color:var(--green)">${jd.stats.accepted || '\u2713'}</span>`;
  }
  if (jd && jd.tiebreaker_triggered) {
    const rej = (jd.rejected_claims || []).length;
    if (rej > 0) return `<span class="step-badge" style="background:var(--amber-bg);color:var(--amber)">-${rej}</span>`;
  }
  return '';
}

function shortModel(name) {
  if (!name) return '';
  return name.replace(/-instruct/g,'').replace(/-q[48]-[a-z0-9-]+/g,'').replace(/-32768|-8192/g,'').split('-').slice(0,3).join('-');
}

// -- Step Selection -------------------------------------------------------
function selectStep(idx) {
  selectedStepIdx = idx;
  renderPipelineFlow(currentExecution.steps);
  renderDetailPanel(currentExecution.steps[idx]);
}

function hideDetailPanel() {
  document.getElementById('detail-panel').classList.remove('visible');
}

// -- Final Output ---------------------------------------------------------
function renderFinalOutput(steps) {
  const el = document.getElementById('final-output');
  const textEl = document.getElementById('final-output-text');
  const gateStep = steps.find(s => s.step_id === 'output_gate');
  const synthStep = steps.findLast(s => s.category === 'synthesize');
  const output = gateStep?.raw_output || synthStep?.raw_output || '';
  if (output) {
    textEl.textContent = output;
    el.classList.add('visible');
  } else {
    el.classList.remove('visible');
  }
}

// -- Execution List Polling -----------------------------------------------
function startExecListPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const executions = await fetchJSON('/api/executions');
      renderExecList(executions);
      autoSelectLive(executions);
    } catch (e) {
      // network blip
    }
  }, 3000);
}

function autoSelectLive(executions) {
  if (liveSource) return;
  if (pinnedExecId) return;
  const live = executions.find(e => e.is_live);
  if (!live) return;
  const card = document.querySelector(`.exec-card[data-exec="${live.execution_id}"]`);
  if (card && !card.classList.contains('active')) {
    loadExecution(card);
  }
}

async function refreshExecList() {
  try {
    const executions = await fetchJSON('/api/executions');
    renderExecList(executions);
  } catch (e) {
    // ignore
  }
}

// -- Helpers --------------------------------------------------------------
function escHtml(str) {
  if (!str) return '';
  const d = document.createElement('div');
  d.textContent = String(str);
  return d.innerHTML;
}

// -- Boot -----------------------------------------------------------------
init().catch(err => {
  document.getElementById('exec-list').innerHTML =
    `<div class="loading" style="color:var(--red)">Error: ${escHtml(err.message)}</div>`;
});

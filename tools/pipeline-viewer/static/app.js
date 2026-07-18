// -- State ----------------------------------------------------------------
let currentExecution = null;
let selectedStepIdx = null;
let pollTimer = null;
let pollInFlight = false;
let execListEtag = null;
const POLL_LIVE_MS = 3000;
const POLL_IDLE_MS = 30000;
let lastPollIntervalMs = POLL_IDLE_MS;
let pinnedExecId = null;  // set on first manual selection; blocks auto-select
let questionStateLocked = false; // true after user manually toggles question

// -- API ------------------------------------------------------------------
async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function fetchExecutionsList() {
  const headers = {};
  if (execListEtag) headers['If-None-Match'] = execListEtag;
  const res = await fetch('/api/executions', { headers });
  if (res.status === 304) return { unchanged: true, executions: null };
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const etag = res.headers.get('ETag');
  if (etag) execListEtag = etag;
  return { unchanged: false, executions: await res.json() };
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
  initCollapsibles();
  const { executions } = await fetchExecutionsList();
  renderExecList(executions);
  startExecListPolling();
}

// -- Collapsibles ---------------------------------------------------------
function initCollapsibles() {
  const header = document.getElementById('question-header');
  if (header) {
    header.addEventListener('click', () => {
      document.getElementById('question-display').classList.toggle('collapsed');
      questionStateLocked = true;
    });
  }
}

function setQuestion(text) {
  const textEl = document.getElementById('question-text');
  const displayEl = document.getElementById('question-display');
  if (!textEl || !displayEl) return;
  if (textEl.textContent !== text) textEl.textContent = text;
  displayEl.style.display = 'block';
  if (!questionStateLocked) {
    displayEl.classList.toggle('collapsed', text.length > 300);
  }
}

function formatUtc(iso) {
  if (!iso) return 'unknown time';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toISOString().replace('.000Z', 'Z').replace('T', ' ');
}

function formatLegacyTimestamp(timestamp) {
  if (!timestamp || !/^\d{8}_\d{6}$/.test(timestamp)) return timestamp || 'unknown time';
  const year = timestamp.slice(0, 4);
  const month = timestamp.slice(4, 6);
  const day = timestamp.slice(6, 8);
  const hour = timestamp.slice(9, 11);
  const minute = timestamp.slice(11, 13);
  const second = timestamp.slice(13, 15);
  return `${year}-${month}-${day} ${hour}:${minute}:${second}Z`;
}

function formatExecutionStart(execution) {
  return execution?.started_at_utc
    ? formatUtc(execution.started_at_utc)
    : formatLegacyTimestamp(execution?.timestamp);
}

function currentModelForStep(step) {
  const activeCallModel = step?.active_model_call?.model || '';
  if (activeCallModel) return activeCallModel;
  const latestCall = step?.model_calls?.length
    ? step.model_calls[step.model_calls.length - 1]?.model
    : '';
  return latestCall || step?.model_ref || step?.model || '';
}

function extractActiveCallsFromSteps(steps) {
  return (steps || [])
    .filter(step => step.status === 'running')
    .map(step => ({
      step_id: step.step_id || '',
      model: step.active_model_call?.model || currentModelForStep(step) || '',
    }))
    .filter(call => call.step_id);
}

function extractFailedCallsFromSteps(steps) {
  const failed = [];
  const seen = new Set();
  for (const step of (steps || [])) {
    let stepHadFailedCall = false;
    for (const call of (step.model_calls || [])) {
      if (call.success !== false) continue;
      const model = call.model || step.model_ref || step.model || '';
      if (!model) continue;
      const key = `${step.step_id}::${model}`;
      if (seen.has(key)) continue;
      seen.add(key);
      failed.push({ step_id: step.step_id || '', model });
      stepHadFailedCall = true;
    }
    if (step.status === 'failed' && !stepHadFailedCall) {
      const model = step.model_ref || step.model || '';
      if (!model) continue;
      const key = `${step.step_id}::${model}`;
      if (seen.has(key)) continue;
      seen.add(key);
      failed.push({ step_id: step.step_id || '', model });
    }
  }
  return failed;
}

function formatActiveCalls(activeCalls) {
  return (activeCalls || []).map((call) => {
    const stepName = displayStepName(call.step_id || '');
    const parent = parentStepLabel(call.step_id || '');
    const label = parent ? `${parent}/${stepName}` : stepName;
    const model = shortModel(call.model || '') || 'resolving...';
    return `${label}: ${model}`;
  });
}

function formatFailedCalls(failedCalls) {
  return (failedCalls || []).map((call) => {
    const stepName = displayStepName(call.step_id || '');
    const parent = parentStepLabel(call.step_id || '');
    const label = parent ? `${parent}/${stepName}` : stepName;
    const model = shortModel(call.model || '') || 'unknown';
    return `${label}: ${model}`;
  });
}

function activeCallsForExecution(execution) {
  return execution?.active_calls || extractActiveCallsFromSteps(execution?.steps);
}

function failedCallsForExecution(execution) {
  return execution?.failed_calls || extractFailedCallsFromSteps(execution?.steps);
}

function buildExecStatusHtml(execution) {
  const activeCalls = formatActiveCalls(activeCallsForExecution(execution));
  const failedCalls = formatFailedCalls(failedCallsForExecution(execution));
  const lines = [];

  if (activeCalls.length) {
    lines.push(`<div class="exec-live-model">${escHtml(activeCalls.join(' · '))}</div>`);
  } else if (execution?.is_live) {
    lines.push('<div class="exec-live-model pending">waiting for first step/model event</div>');
  }

  if (failedCalls.length) {
    lines.push(`<div class="exec-failed-model">${escHtml(failedCalls.join(' · '))}</div>`);
  }

  return lines.join('');
}

function renderExecCard(execution) {
  const liveTag = execution.is_live ? '<span class="live-badge">LIVE</span>' : '';
  const pipelineLabel = escHtml(execution.pipeline_id);
  const startedAt = escHtml(formatExecutionStart(execution));
  const expandToggle = execution.question.length > 150
    ? `<a href="#" class="expand-toggle" onclick="toggleQuestion(event)">Show more</a>`
    : '';
  const statusHtml = buildExecStatusHtml(execution);
  const statusBlock = `<div class="exec-status-lines">${statusHtml}</div>`;

  return `
      <div class="exec-card ${execution.is_live ? 'live' : ''}"
           data-pipeline="${execution.pipeline_id}" data-exec="${execution.execution_id}"
           data-live="${execution.is_live}" onclick="loadExecution(this)">
        <div class="question">${liveTag}${escHtml(execution.question)}</div>
        ${expandToggle}
        ${statusBlock}
        <div class="meta">${execution.step_count} steps &middot; ${startedAt} &middot; <span class="pipeline-label">${pipelineLabel}</span></div>
      </div>
    `;
}

function updateExecutionCard(execution) {
  const card = document.querySelector(`.exec-card[data-exec="${execution?.execution_id}"]`);
  if (!card) return;
  const statusLines = card.querySelector('.exec-status-lines');
  const meta = card.querySelector('.meta');
  const stepCount = execution?.step_count ?? execution?.steps?.length ?? 0;

  if (statusLines) statusLines.innerHTML = buildExecStatusHtml(execution);
  if (meta) {
    meta.innerHTML = `${stepCount} steps &middot; ${escHtml(formatExecutionStart(execution))} &middot; <span class="pipeline-label">${escHtml(execution.pipeline_id || '')}</span>`;
  }
  card.dataset.live = execution?.is_live ? 'true' : 'false';
}

function runningStepModels(steps) {
  return extractActiveCallsFromSteps(steps)
    .map(call => ({
      stepId: displayStepName(call.step_id),
      model: shortModel(call.model) || 'resolving...',
      parent: parentStepLabel(call.step_id),
    }));
}

function renderExecutionMeta(execution) {
  const el = document.getElementById('execution-meta');
  if (!el || !execution) return;

  const startedAt = formatExecutionStart(execution);
  const running = runningStepModels(execution.steps);
  const chips = [
    `<div class="execution-chip"><span class="chip-label">Called</span><span class="chip-value">${escHtml(startedAt)}</span></div>`,
  ];

  if (running.length) {
    const runningText = running.map(({ stepId, model, parent }) => {
      const stepName = parent ? `${parent}/${stepId}` : stepId;
      return `${stepName}: ${model}`;
    }).join(' · ');
    chips.push(
      `<div class="execution-chip live"><span class="chip-label">Running</span><span class="chip-value">${escHtml(runningText)}</span></div>`
    );
  }

  el.innerHTML = chips.join('');
  el.classList.add('visible');
}

// -- Execution List -------------------------------------------------------
function renderExecList(executions) {
  const container = document.getElementById('exec-list');
  if (!executions.length) {
    container.innerHTML = '<div class="loading">No executions found.</div>';
    return;
  }
  container.innerHTML = executions.map(renderExecCard).join('');

  if (pinnedExecId) {
    document.querySelector(`.exec-card[data-exec="${pinnedExecId}"]`)?.classList.add('active');
  }
}

function toggleQuestion(event) {
  event.preventDefault();
  event.stopPropagation();
  const toggle = event.target;
  const questionDiv = toggle.closest('.exec-card')?.querySelector('.question');
  if (!questionDiv) return;
  const expanded = questionDiv.classList.toggle('expanded');
  toggle.textContent = expanded ? 'Show less' : 'Show more';
}

async function loadExecution(el) {
  disconnectStream();
  document.querySelectorAll('.exec-card').forEach(c => c.classList.remove('active'));
  el.classList.add('active');

  const pid = el.dataset.pipeline;
  const eid = el.dataset.exec;
  const isLive = el.dataset.live === 'true';
  pinnedExecId = eid;
  questionStateLocked = false;

  if (isLive) {
    connectStream(pid, eid);
    scrollToExecutionDetails();
  } else {
    currentExecution = await fetchJSON(`/api/executions/${pid}/${eid}`);
    currentExecution.is_live = false;
    currentExecution.step_count = currentExecution.steps?.length || 0;
    selectedStepIdx = null;
    renderFullExecution();
    updateExecutionCard(currentExecution);
    scrollToExecutionDetails();
  }
}

function scrollToExecutionDetails() {
  const metaEl = document.getElementById('execution-meta');
  const questionEl = document.getElementById('question-display');
  const target = metaEl?.classList.contains('visible') ? metaEl : questionEl;
  target?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// -- Static rendering (completed executions) ------------------------------
function renderFullExecution() {
  setLiveIndicator(false);
  setQuestion(currentExecution.question);
  renderExecutionMeta(currentExecution);
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
  const promptKB = (summary.prompt_text_bytes || 0) / 1024;
  const promptKBStr = promptKB >= 100
    ? `${promptKB.toFixed(0)}KB`
    : `${promptKB.toFixed(1)}KB`;

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
    <div class="stat"><span class="stat-value">${promptKBStr}</span><span class="stat-label">Prompt Text</span></div>
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
  const wallStr = step.latency_ms ? `${(step.latency_ms/1000).toFixed(1)}s` : null;
  const hasInference = step.inference_ms > 0;
  const isParallel = hasInference && step.latency_ms && step.inference_ms > step.latency_ms;
  const latency = hasInference
    ? `${(step.inference_ms/1000).toFixed(1)}s (${wallStr})`
    : (wallStr ?? '-');
  const latencyTitle = hasInference
    ? (isParallel
        ? 'Σ cumulative inference across parallel model calls (wall clock)'
        : 'Cumulative inference time (wall clock)')
    : null;
  const tokens = step.tokens.total || 0;
  const hasFailed = !!step.error;
  const tokStr = hasFailed
    ? 'ERROR'
    : (step.status === 'running'
        ? 'RUNNING'
        : (tokens > 1000 ? `${(tokens/1000).toFixed(0)}K tok` : `${tokens} tok`));
  const model = shortModel(currentModelForStep(step));
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
      <div class="step-meta"${latencyTitle ? ` title="${latencyTitle}"` : ''}>${model} &middot; ${latency} &middot; ${tokStr}</div>
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
function execListPollIntervalMs(executions) {
  lastPollIntervalMs = (executions || []).some(e => e.is_live) ? POLL_LIVE_MS : POLL_IDLE_MS;
  return lastPollIntervalMs;
}

function scheduleExecListPoll(delayMs) {
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = setTimeout(pollExecListOnce, delayMs);
}

async function pollExecListOnce() {
  if (pollInFlight) {
    scheduleExecListPoll(POLL_LIVE_MS);
    return;
  }
  pollInFlight = true;
  let nextDelay = POLL_IDLE_MS;
  try {
    const { unchanged, executions } = await fetchExecutionsList();
    if (!unchanged) {
      renderExecList(executions);
      autoSelectLive(executions);
      nextDelay = execListPollIntervalMs(executions);
    } else {
      nextDelay = lastPollIntervalMs;
    }
  } catch (e) {
    // network blip
  } finally {
    pollInFlight = false;
    scheduleExecListPoll(nextDelay);
  }
}

function startExecListPolling() {
  if (pollTimer) clearTimeout(pollTimer);
  document.addEventListener('visibilitychange', onExecListVisibilityChange);
  scheduleExecListPoll(POLL_LIVE_MS);
}

function onExecListVisibilityChange() {
  if (document.visibilityState !== 'visible') return;
  if (pollTimer) clearTimeout(pollTimer);
  pollExecListOnce();
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
    const { unchanged, executions } = await fetchExecutionsList();
    if (!unchanged) renderExecList(executions);
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

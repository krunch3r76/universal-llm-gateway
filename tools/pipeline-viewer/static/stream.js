// -- SSE Live Streaming & Client-Side Event Aggregation -------------------
// Depends on: escHtml(), renderSummaryBanner(), renderPipelineFlow(),
//   renderFinalOutput(), hideDetailPanel(), setLiveIndicator(), refreshExecList()
//   from app.js

let liveSource = null;
let liveEvents = [];

function connectStream(pipelineId, execId) {
  liveEvents = [];
  currentExecution = null;
  selectedStepIdx = null;

  document.getElementById('question-text').textContent = 'Waiting for events...';
  document.getElementById('question-display').style.display = 'block';
  document.getElementById('summary-banner').classList.remove('visible');
  document.getElementById('pipeline-flow').classList.remove('visible');
  document.getElementById('final-output').classList.remove('visible');
  hideDetailPanel();
  setLiveIndicator(true);

  const url = `/api/executions/${pipelineId}/${execId}/stream`;
  liveSource = new EventSource(url);

  liveSource.onmessage = (msg) => {
    try {
      const ev = JSON.parse(msg.data);
      liveEvents.push(ev);
      currentExecution = aggregateClientSide(liveEvents);
      renderLiveUpdate();
    } catch (e) {
      // skip malformed lines
    }
  };

  liveSource.addEventListener('done', () => {
    disconnectStream();
    setLiveIndicator(false);
    if (currentExecution) renderFinalOutput(currentExecution.steps);
    refreshExecList();
  });

  liveSource.addEventListener('error', () => {
    disconnectStream();
    setLiveIndicator(false);
  });
}

function disconnectStream() {
  if (liveSource) {
    liveSource.close();
    liveSource = null;
  }
}

function renderLiveUpdate() {
  if (!currentExecution) return;
  document.getElementById('question-text').textContent = currentExecution.question;
  renderSummaryBanner(currentExecution.summary);
  renderPipelineFlow(currentExecution.steps);
}

// -- Client-side event aggregation (mirrors Python aggregator.py) ---------

function aggregateClientSide(events) {
  if (!events.length) return null;
  const first = events[0];
  const started = events.find(e => e.event_type === 'pipeline_started');
  const question = started?.source_text || 'Processing...';
  const stepData = {};
  const stepOrder = [];

  for (const ev of events) {
    const sname = ev.step_name || '';
    if (!sname) continue;
    if (!stepData[sname]) {
      stepData[sname] = {
        step_id: sname, step_type: null, model: null, model_ref: null,
        latency_ms: null, inference_ms: 0, status: 'pending',
        tokens: { prompt: 0, completion: 0, total: 0 },
        inputs: {}, raw_output: null, json_data: null,
        iterations: null, domain_routing: null,
        model_calls: [], error: null, traceback: null,
      };
      stepOrder.push(sname);
    }
    applyEvent(stepData[sname], ev);
  }

  const steps = stepOrder.map((sname, i) => {
    const sd = stepData[sname];
    sd.step_number = i + 1;
    sd.category = categorizeStep(sd.step_id);
    return sd;
  });

  inferVerifierPool(steps);

  const completed = events.find(e => e.event_type === 'pipeline_completed');
  const lastEvent = events[events.length - 1];
  const wallClockMs = completed?.duration_ms ?? lastEvent?.timestamp_ms ?? null;

  return {
    pipeline_id: first.pipeline_id || '',
    execution_id: first.execution_id || '',
    question,
    steps,
    summary: buildSummary(steps, wallClockMs),
  };
}

function applyEvent(sd, ev) {
  switch (ev.event_type || '') {
    case 'step_started':
      sd.step_type = ev.step_type || sd.step_type;
      sd.status = 'running';
      if (ev.model_id) { sd.model = ev.model_id; sd.model_ref = ev.model_id; }
      break;
    case 'step_inputs_captured':
      sd.inputs = ev.inputs || {};
      break;
    case 'step_output_captured':
      sd.raw_output = ev.raw || sd.raw_output;
      sd.json_data = ev.json_data || sd.json_data;
      sd.latency_ms = ev.latency_ms || sd.latency_ms;
      if (ev.model_id) sd.model = ev.model_id;
      // For map steps, iterations already accumulated tokens; do not overwrite.
      if ((ev.prompt_tokens != null || ev.completion_tokens != null) && !sd.iterations) {
        sd.tokens = {
          prompt: ev.prompt_tokens || 0,
          completion: ev.completion_tokens || 0,
          total: (ev.prompt_tokens || 0) + (ev.completion_tokens || 0),
        };
      }
      if (ev.system_prompt) sd.system_prompt = ev.system_prompt;
      if (ev.user_prompt) sd.user_prompt = ev.user_prompt;
      if (ev.request_body) sd.request_body = ev.request_body;
      // Convention-based enrichment from well-known StepOutput.json fields
      if (sd.json_data && typeof sd.json_data === 'object') {
        const jd = sd.json_data;
        if (jd.authority_verdicts && !sd.domain_routing) {
          sd.domain_routing = {
            authority_verdicts: jd.authority_verdicts,
            claims_routed_to_general: (jd.claims_for_general || []).map(
              c => c.statement_id || ''
            ),
          };
        }
        if (jd.verified_facts && jd.rejected_claims && !jd.stats) {
          jd.stats = {
            total_claims: jd.verified_facts.length + jd.rejected_claims.length,
            accepted: jd.verified_facts.length,
            rejected: jd.rejected_claims.length,
          };
        }
        if (jd.history && Array.isArray(jd.history) && !sd.iterations) {
          sd.iterations = jd.history.map((h, i) => ({
            index: h.iteration ?? i,
            action: h.action || '',
            reason: h.reason || '',
            is_terminal: !!h.is_terminal,
            model: h.model || '',
            assess_latency_ms: h.assess_latency_ms || 0,
            action_latency_ms: h.action_latency_ms || 0,
            latency_ms: (h.assess_latency_ms || 0) + (h.action_latency_ms || 0),
            prompt_tokens: h.prompt_tokens || 0,
            completion_tokens: h.completion_tokens || 0,
          }));
        }
      }
      break;
    case 'step_completed':
      sd.status = 'completed';
      if (ev.duration_ms) sd.latency_ms = ev.duration_ms;
      if (ev.prompt_tokens != null) {
        if (!sd.tokens) sd.tokens = { prompt: 0, completion: 0, total: 0 };
        const comp = ev.completion_tokens ?? 0;
        sd.tokens.prompt = ev.prompt_tokens;
        sd.tokens.completion = comp;
        sd.tokens.total = ev.prompt_tokens + comp;
      }
      break;
    case 'step_failed':
      sd.status = 'failed';
      sd.error = ev.error || null;
      sd.traceback = ev.traceback || null;
      if (ev.duration_ms) sd.latency_ms = ev.duration_ms;
      break;
    case 'model_invocation':
      if (!sd.model_calls) sd.model_calls = [];
      const callInferenceMs = ev.inference_ms || 0;
      sd.model_calls.push({
        call_label: ev.call_label || '',
        model: ev.model_id || '',
        snapshot_request_id: ev.snapshot_request_id || '',
        system_prompt: ev.system_prompt || null,
        user_prompt: ev.user_prompt || '',
        request_body: ev.request_body || null,
        response_text: ev.response_text || null,
        error: ev.error || null,
        latency_ms: ev.latency_ms || 0,
        inference_ms: callInferenceMs,
        prompt_tokens: ev.prompt_tokens || 0,
        completion_tokens: ev.completion_tokens || 0,
        success: ev.success !== false,
        wall_clock: ev.wall_clock || '',
        metadata: ev.metadata || null,
      });
      sd.inference_ms = (sd.inference_ms || 0) + callInferenceMs;
      break;
    case 'step_skipped':
      sd.status = 'skipped';
      break;
    case 'map_iteration_completed': {
      if (!sd.iterations) sd.iterations = [];
      const iterPrompt = ev.prompt_tokens || 0;
      const iterCompletion = ev.completion_tokens || 0;
      sd.iterations.push({
        index: ev.iteration_index || 0,
        model: ev.model_id || '',
        latency_ms: ev.duration_ms || 0,
        output: ev.output_text || '',
        prompt_tokens: iterPrompt,
        completion_tokens: iterCompletion,
      });
      // Accumulate iteration tokens into step totals
      sd.tokens.prompt += iterPrompt;
      sd.tokens.completion += iterCompletion;
      sd.tokens.total = sd.tokens.prompt + sd.tokens.completion;
      break;
    }
    case 'verification_complete': {
      const prevCd = sd.json_data && sd.json_data.compound_decomposition;
      sd.json_data = {
        verified_facts: ev.verified_facts || [],
        rejected_claims: ev.rejected_claims || [],
        verdicts_by_model: ev.verdicts_by_model || {},
        verifier_pool: ev.verifier_pool || [],
        originator: ev.originator || '',
        stats: ev.stats || {},
        answer_sentences: ev.answer_sentences || [],
      };
      if (prevCd != null) sd.json_data.compound_decomposition = prevCd;
      break;
    }
    case 'domain_verification_completed':
      sd.domain_routing = {
        authority_verdicts: ev.authority_verdicts || {},
        claims_routed_to_general: ev.claims_routed_to_general || [],
      };
      break;
    case 'compound_claims_decomposed':
      if (!sd.json_data) sd.json_data = {};
      sd.json_data.compound_decomposition = {
        decomposed_count: ev.decomposed_count || 0,
        total_sub_claims: ev.total_sub_claims || 0,
        decompose_latency_ms: ev.decompose_latency_ms || 0,
        details: ev.details || [],
      };
      break;
    case 'assess_loop_started':
      if (!sd.json_data) sd.json_data = {};
      sd.json_data.assess_loop = {
        max_iterations: ev.max_iterations || 0,
        terminal_action: ev.terminal_action || '',
        action_names: ev.action_names || null,
      };
      break;
    case 'assess_loop_iteration_completed': {
      if (!sd.iterations) sd.iterations = [];
      const aMs = ev.assess_latency_ms || 0;
      const actMs = ev.action_latency_ms || 0;
      sd.iterations.push({
        index: ev.iteration || 0,
        action: ev.action || '',
        reason: ev.reason || '',
        is_terminal: !!ev.is_terminal,
        model: ev.action_model_id || '',
        assess_latency_ms: aMs,
        action_latency_ms: actMs,
        latency_ms: aMs + actMs,
        prompt_tokens: ev.iteration_prompt_tokens || 0,
        completion_tokens: ev.iteration_completion_tokens || 0,
      });
      break;
    }
    case 'assess_loop_completed':
      if (!sd.json_data) sd.json_data = {};
      sd.json_data.exit_reason = ev.exit_reason || '';
      sd.json_data.iterations_used = ev.iterations_used || 0;
      sd.json_data.total_model_calls = ev.total_model_calls || 0;
      break;
    case 'tiebreaker_triggered':
      if (!sd.json_data) sd.json_data = {};
      sd.json_data.tiebreaker_triggered = {
        borderline_claim_ids: ev.borderline_claim_ids || [],
        tiebreaker_model: ev.tiebreaker_model || '',
        total_claims: ev.total_claims || 0,
        math_excluded: ev.math_excluded || 0,
      };
      break;
  }
}

function categorizeStep(stepId) {
  // Sub-pipeline steps: categorize by parent prefix
  if (stepId.includes('__')) {
    const parent = stepId.split('__')[0];
    if (parent.includes('verify') || parent.includes('veto')) return 'verify';
    if (parent.includes('synth')) return 'synthesize';
    stepId = stepId.split('__')[1];
  }
  if (stepId.includes('analyze') || stepId.includes('classify')) return 'classify';
  if (stepId.includes('answer') || stepId.includes('reseed')) return 'answer';
  if (stepId.includes('verify') || stepId.includes('tiebreaker')) return 'verify';
  if (stepId.includes('enrich')) return 'enrich';
  if (stepId.includes('synth') || stepId.includes('post_process')) return 'synthesize';
  if (stepId.includes('output_gate')) return 'gate';
  return 'other';
}

function buildSummary(steps, wallClockMs) {
  let totalPrompt = 0, totalCompletion = 0, summedLatency = 0;
  let totalClaims = 0, totalAccepted = 0, totalRejected = 0;
  let totalModelCalls = 0;
  const models = new Set();

  for (const step of steps) {
    const tok = step.tokens || {};
    totalPrompt += tok.prompt || 0;
    totalCompletion += tok.completion || 0;
    if (step.latency_ms) summedLatency += step.latency_ms;
    if (step.model) models.add(step.model);
    if (step.iterations) {
      totalModelCalls += step.iterations.length;
      for (const it of step.iterations) { if (it.model) models.add(it.model); }
    }
    const jd = step.json_data;
    if (jd?.stats) {
      totalClaims += jd.stats.total_claims || 0;
      totalAccepted += jd.stats.accepted || 0;
      totalRejected += jd.stats.rejected || 0;
    } else if (jd?.verified_facts) {
      const nv = jd.verified_facts.length;
      const nr = (jd.rejected_claims || []).length;
      totalClaims += nv + nr;
      totalAccepted += nv;
      totalRejected += nr;
    }
    if (jd?.verdicts_by_model) {
      totalModelCalls += Object.keys(jd.verdicts_by_model).length;
    }
  }

  return {
    total_tokens: totalPrompt + totalCompletion,
    prompt_tokens: totalPrompt,
    completion_tokens: totalCompletion,
    wall_clock_ms: wallClockMs,
    total_latency_ms: wallClockMs != null ? wallClockMs : summedLatency,
    summed_latency_ms: summedLatency,
    total_steps: steps.length,
    models_used: [...models].sort(),
    total_claims_verified: totalClaims,
    total_accepted: totalAccepted,
    total_rejected: totalRejected,
    total_model_calls: totalModelCalls,
  };
}

function inferVerifierPool(steps) {
  // Infer full verifier pool from the union of all voted model IDs
  // across verify steps. Per-step, the single missing member is the originator.
  const globalPool = new Set();
  const verifySteps = [];
  for (const step of steps) {
    const jd = step.json_data;
    if (!jd || !jd.verdicts_by_model) continue;
    for (const m of Object.keys(jd.verdicts_by_model)) globalPool.add(m);
    verifySteps.push(step);
  }
  if (!globalPool.size) return;

  const sortedPool = [...globalPool].sort();
  for (const step of verifySteps) {
    const jd = step.json_data;
    if (jd.verifier_pool && jd.verifier_pool.length) continue;
    const voters = new Set(Object.keys(jd.verdicts_by_model));
    const missing = sortedPool.filter(m => !voters.has(m));
    jd.verifier_pool = sortedPool;
    if (missing.length === 1) jd.originator = missing[0];
  }
}

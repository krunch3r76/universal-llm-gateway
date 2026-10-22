"""Shared Cursor Glass instantiationService lookup and composer CDP scripts."""

from __future__ import annotations

import json

GLASS_SERVICE_MAP_FN = r"""
function glassServiceMap(startEl) {
  const fiberKey = (el) => Object.keys(el).find((k) => k.startsWith('__reactFiber$')) || null;
  let el = startEl;
  if (!fiberKey(el) && el.parentElement) el = el.parentElement;
  const fk = fiberKey(el);
  if (!fk) return null;
  let f = el[fk];
  for (let i = 0; i < 250 && f; i++, f = f.return) {
    const entries = f.memoizedProps && f.memoizedProps.value && f.memoizedProps.value.workspace
      && f.memoizedProps.value.workspace.instantiationService
      && f.memoizedProps.value.workspace.instantiationService._services
      && f.memoizedProps.value.workspace.instantiationService._services._entries;
    if (!entries || typeof entries.entries !== 'function') continue;
    const byId = new Map();
    for (const [k, ent] of entries.entries()) {
      const inst = ent && (ent.instance !== undefined ? ent.instance : ent);
      byId.set(String(k), inst);
    }
    return byId;
  }
  return null;
}
"""


RESOLVE_WORKSPACE_SERVICES_FN = r"""
function resolveWorkspaceServices(rootMap, agentId) {
  const extractEnv = (h) => (h && ((h.environment && h.environment._value) || h.environment
    || (h.location && h.location.environment) || h.workspaceIdentifier)) || null;
  const isUlgEnv = (env) => String((env && env.uri && (env.uri.fsPath || env.uri.path)) || '')
    .includes('universal-llm-gateway');
  const instPath = (inst) => {
    const wi = inst && inst.workspaceIdentifier;
    const uri = wi && wi.uri;
    return String((uri && (uri.fsPath || uri.path)) || inst.folderUri || inst.displayName
      || inst.name || '');
  };
  const repo = rootMap.get('agentRepositoryService');
  const wcs = rootMap.get('workspaceCollectionService');
  const glassActive = rootMap.get('glassActiveAgentService');
  let activeId = '';
  if (glassActive && typeof glassActive.getActiveAgentId === 'function') {
    try { activeId = String(glassActive.getActiveAgentId() || '').trim(); } catch (e) {}
  }
  if (!activeId) {
    const activeRow = document.querySelector('[data-perf-id="glass-sidebar-agent-row"][data-active="true"]')
      || document.querySelector('.ui-sidebar-menu-button-trigger[aria-current="page"]')
        ?.closest('[data-perf-id="glass-sidebar-agent-row"]');
    if (activeRow) {
      activeId = (activeRow.getAttribute('data-sidebar-file-drop-agent-id')
        || activeRow.getAttribute('data-agent-id') || '').trim();
    }
  }
  const resolvedAgentId = String(agentId || activeId || '').trim();
  const explicitAgentId = String(agentId || '').trim();
  let targetEnv = null;
  let matchedInst = null;
  if (explicitAgentId && repo && typeof repo.getAgentHeader === 'function') {
    try {
      const env = extractEnv(repo.getAgentHeader(explicitAgentId));
      if (env) targetEnv = env;
    } catch (e) {}
  }
  if (!targetEnv && wcs && typeof wcs.getWorkspaceInstances === 'function') {
    try {
      const instances = wcs.getWorkspaceInstances();
      const list = Array.isArray(instances) ? instances : (instances ? [...instances] : []);
      const ulg = list.filter((inst) => isUlgEnv(inst && inst.workspaceIdentifier)
        || instPath(inst).includes('universal-llm-gateway'));
      if (ulg.length > 0) {
        matchedInst = ulg.find((inst) => {
          const uri = inst && inst.workspaceIdentifier && inst.workspaceIdentifier.uri;
          return uri && uri.scheme === 'vscode-remote';
        }) || ulg.find((inst) => {
          const uri = inst && inst.workspaceIdentifier && inst.workspaceIdentifier.uri;
          return !uri || uri.scheme !== 'empty-window';
        }) || ulg[0];
        targetEnv = matchedInst && (matchedInst.workspaceIdentifier || matchedInst.identifier);
      }
      if (!targetEnv && repo && activeId && typeof repo.getAgentHeader === 'function') {
        try {
          const env = extractEnv(repo.getAgentHeader(activeId));
          if (env) targetEnv = env;
        } catch (e) {}
      }
      if (!targetEnv) {
        const nonEmpty = list.find((inst) => {
          const wi = inst && inst.workspaceIdentifier;
          return wi && wi.id !== 'empty-window';
        });
        if (nonEmpty) {
          matchedInst = nonEmpty;
          targetEnv = nonEmpty.workspaceIdentifier || nonEmpty.identifier;
        }
      }
    } catch (e) {}
  }
  let merged = rootMap;
  const mergeFromInst = (wsInst) => {
    const entries = wsInst && wsInst.instantiationService
      && wsInst.instantiationService._services
      && wsInst.instantiationService._services._entries;
    if (entries && typeof entries.entries === 'function') {
      merged = new Map(rootMap);
      for (const [k, ent] of entries.entries()) {
        const inst = ent && (ent.instance !== undefined ? ent.instance : ent);
        merged.set(String(k), inst);
      }
    }
  };
  if (matchedInst && matchedInst.instantiationService) {
    try { mergeFromInst(matchedInst); } catch (e) {}
  } else if (wcs && targetEnv && typeof wcs.getWorkspaceInstance === 'function') {
    try { mergeFromInst(wcs.getWorkspaceInstance(targetEnv)); } catch (e) {}
  }
  return { services: merged, targetEnv, agentId: resolvedAgentId };
}
"""


def _resolve_catalog_js() -> str:
    return r"""
async function resolveCatalog(modelConfigService, label) {
  const q = String(label || '').trim();
  if (!q) return null;
  const slugShape = (id) => typeof id === 'string' && id.length > 0 && !/\s/.test(id);
  const parseEffort = (text) => {
    const lower = text.toLowerCase();
    if (/\bxhigh\b|extra\s+high/.test(lower)) return 'xhigh';
    if (/\bhigh\b/.test(lower)) return 'high';
    if (/\bmedium\b/.test(lower)) return 'medium';
    if (/\blow\b/.test(lower)) return 'low';
    return null;
  };
  const mapFromLabel = (labelStr) => {
    const lower = labelStr.toLowerCase();
    const effort = parseEffort(lower);
    if (lower.includes('gemini') && lower.includes('4') && lower.includes('argon')) {
      return {
        modelId: 'gemini-4-argon',
        parameters: [{ id: 'reasoning_effort', value: effort || 'low' }],
      };
    }
    if (lower.includes('grok') && lower.includes('4.7')) {
      return {
        modelId: 'grok-4.7',
        parameters: [
          { id: 'context', value: '256k' },
          { id: 'reasoning_effort', value: effort || 'xhigh' },
          { id: 'fast', value: 'false' },
        ],
      };
    }
    if (lower.includes('composer') && lower.includes('2.5')) {
      return { modelId: 'composer-2.5', parameters: [] };
    }
    if (lower.includes('opus') && (lower.includes('5.5') || lower.includes('5-5'))) {
      return {
        modelId: 'claude-opus-5-5',
        parameters: [{ id: 'reasoning_effort', value: effort || 'high' }],
      };
    }
    if (lower.includes('opus') && lower.includes('5')
        && !lower.includes('5.5') && !lower.includes('5-5')) {
      return {
        modelId: 'claude-opus-5',
        parameters: [{ id: 'reasoning_effort', value: effort || 'high' }],
      };
    }
    if (lower.includes('sonnet') && (lower.includes('5.5') || lower.includes('5-5'))) {
      return {
        modelId: 'claude-sonnet-5-5',
        parameters: [{ id: 'reasoning_effort', value: effort || 'high' }],
      };
    }
    if (lower.includes('fable') && (lower.includes('5.1') || lower.includes('5-1'))) {
      return {
        modelId: 'claude-fable-5-1',
        parameters: [{ id: 'reasoning_effort', value: effort || 'xhigh' }],
      };
    }
    const stripped = labelStr.replace(/\s+(extra\s+high|xhigh|high|medium|low|fast)$/i, '').trim();
    const slug = stripped.toLowerCase().replace(/\s+/g, '-');
    if (slugShape(slug)) {
      const params = effort ? [{ id: 'reasoning_effort', value: effort }] : [];
      return { modelId: slug, parameters: params };
    }
    return null;
  };
  if (modelConfigService && typeof modelConfigService.resolveModelNameToCatalog === 'function') {
    try {
      const cat = await modelConfigService.resolveModelNameToCatalog(q);
      if (cat && typeof cat === 'object') {
        if (slugShape(cat.modelId)) {
          return { modelId: cat.modelId, parameters: cat.parameters || [] };
        }
        if (slugShape(cat.modelName)) {
          return { modelId: cat.modelName, parameters: cat.parameters || [] };
        }
        const mapped = mapFromLabel(q);
        if (mapped) return mapped;
      }
      if (typeof cat === 'string') {
        if (slugShape(cat)) return { modelId: cat, parameters: [] };
        const mapped = mapFromLabel(q) || mapFromLabel(cat);
        if (mapped) return mapped;
      }
    } catch (e) { /* fall through */ }
  }
  return mapFromLabel(q) || { modelId: q, parameters: [] };
}
"""


APPLY_HOUSE_KNOBS_FN = r"""
function applyHouseKnobs(cat, knobs) {
  if (!cat || !cat.modelId || !knobs || typeof knobs !== 'object') return cat;
  const params = Array.isArray(cat.parameters)
    ? cat.parameters.map((p) => (p ? { ...p } : p))
    : [];
  const setParam = (id, value) => {
    if (value === undefined || value === null || String(value).trim() === '') return;
    const s = String(value);
    const idx = params.findIndex((p) => p && p.id === id);
    const entry = { id, value: s };
    if (idx >= 0) params[idx] = entry;
    else params.push(entry);
  };
  if (knobs.effort != null && String(knobs.effort).trim() !== '') {
    setParam('reasoning_effort', knobs.effort);
  }
  if (knobs.fast != null && String(knobs.fast).trim() !== '') {
    setParam('fast', knobs.fast);
  }
  return { modelId: cat.modelId, parameters: params };
}
"""


def launch_new_agent_tab_js(
    message: str,
    model_query: str,
    model_knobs: dict[str, str] | None = None,
    requested_agent_id: str | None = None,
) -> str:
    """CDP expression: create agent tab, set model from label plus house knobs, submit."""
    msg = json.dumps(message)
    mq = json.dumps(str(model_query or "").strip())
    if model_knobs is not None:
        knobs_line = f"  const houseKnobs = {json.dumps(model_knobs)};\n"
    else:
        knobs_line = "  const houseKnobs = null;\n"
    req_id = json.dumps(str(requested_agent_id or "").strip())
    return (
        "(async () => {\n"
        f"{GLASS_SERVICE_MAP_FN}\n"
        f"{RESOLVE_WORKSPACE_SERVICES_FN}\n"
        f"{_resolve_catalog_js()}\n"
        f"{APPLY_HOUSE_KNOBS_FN}\n"
        f"  const requestedAgentId = {req_id};\n"
        "  const task = async () => {\n"
        "  const text = "
        f"{msg};\n"
        "  const modelLabel = "
        f"{mq};\n"
        f"{knobs_line}"
        "  const anchor = document.querySelector('[data-perf-id=\"glass-sidebar-agent-row\"]')"
        ' || document.querySelector(\'[aria-label="Prompt"][contenteditable="true"]\');\n'
        "  if (!anchor) return {ok: false, reason: 'no_anchor'};\n"
        "  const byId = glassServiceMap(anchor);\n"
        "  if (!byId) return {ok: false, reason: 'no_services'};\n"
        "  const agentRepositoryService = byId.get('agentRepositoryService');\n"
        "  const glassActiveAgentService = byId.get('glassActiveAgentService');\n"
        "  const composerService = byId.get('composerService');\n"
        "  const composerDataService = byId.get('composerDataService');\n"
        "  const modelConfigService = byId.get('modelConfigService');\n"
        "  const composerChatService = byId.get('composerChatService');\n"
        "  let modelConfig = null;\n"
        "  if (modelLabel && modelConfigService) {\n"
        "    const catRaw = await resolveCatalog(modelConfigService, modelLabel);\n"
        "    const cat = houseKnobs ? applyHouseKnobs(catRaw, houseKnobs) : catRaw;\n"
        "    if (cat && cat.modelId) {\n"
        "      modelConfig = {\n"
        "        modelName: cat.modelId,\n"
        "        maxMode: false,\n"
        "        selectedModels: [{ modelId: cat.modelId, parameters: cat.parameters || [] }],\n"
        "      };\n"
        "    }\n"
        "  }\n"
        "  const { targetEnv } = resolveWorkspaceServices(byId, '');\n"
        "  if (requestedAgentId && agentRepositoryService"
        " && typeof agentRepositoryService.getAgentHeader === 'function') {\n"
        "    try {\n"
        "      const existing = agentRepositoryService.getAgentHeader(requestedAgentId);\n"
        "      if (existing) {\n"
        "        return {ok: true, agent_id: requestedAgentId, editor_agent_id: requestedAgentId,\n"
        "          transport: 'cdp_glass_services', method: 'already_created'};\n"
        "      }\n"
        "    } catch (e) {}\n"
        "  }\n"
        "  if (agentRepositoryService && typeof agentRepositoryService.createAgent === 'function'"
        " && targetEnv) {\n"
        "    const created = await agentRepositoryService.createAgent(\n"
        "      text,\n"
        "      { type: 'existing', environment: targetEnv },\n"
        "      {\n"
        "        composerId: requestedAgentId || undefined,\n"
        "        modelConfig,\n"
        "        destination: { type: 'local' },\n"
        "        promptPreview: text.slice(0, 120),\n"
        "        richText: text,\n"
        "        unifiedMode: 'agent',\n"
        "        skipFocusAfterSubmission: false,\n"
        "      },\n"
        "    );\n"
        "    const agentId = (typeof created === 'string' ? created\n"
        "      : (created && ((created.header && created.header.id)\n"
        "        || (created._inner && created._inner.agentId) || created.agentId\n"
        "        || created.composerId || created.id || created.stableId))) || '';\n"
        "    if (!agentId) return {ok: false, reason: 'no_agent_id'};\n"
        "    if (glassActiveAgentService && typeof glassActiveAgentService.setActiveAgentId === 'function') {\n"
        "      await glassActiveAgentService.setActiveAgentId(agentId);\n"
        "    }\n"
        "    return {ok: true, agent_id: agentId, editor_agent_id: agentId,\n"
        "      transport: 'cdp_glass_services', method: 'createAgent'};\n"
        "  }\n"
        "  if (!composerService || !composerDataService || !composerChatService) {\n"
        "    return {ok: false, reason: 'missing_services'};\n"
        "  }\n"
        "  const created = await composerService.createComposer({\n"
        "    skipShowAndFocus: true,\n"
        "    skipFocus: true,\n"
        "    skipSelect: true,\n"
        "    openInNewTab: true,\n"
        "    unifiedMode: 'agent',\n"
        "    partialState: { unifiedMode: 'agent' },\n"
        "  });\n"
        "  const agentId = created && created.composerId;\n"
        "  if (!agentId) return {ok: false, reason: 'no_composer_id'};\n"
        "  const handle = await composerDataService.getComposerHandleById(agentId);\n"
        "  if (!handle) return {ok: false, reason: 'no_handle', agent_id: agentId};\n"
        "  if (modelLabel && modelConfigService) {\n"
        "    const catRaw = await resolveCatalog(modelConfigService, modelLabel);\n"
        "    const cat = houseKnobs ? applyHouseKnobs(catRaw, houseKnobs) : catRaw;\n"
        "    if (cat && cat.modelId) {\n"
        "      modelConfigService.setModelConfigForComposer(handle, {\n"
        "        modelName: cat.modelId,\n"
        "        maxMode: false,\n"
        "        selectedModels: [{ modelId: cat.modelId, parameters: cat.parameters || [] }],\n"
        "      }, 'composer');\n"
        "    }\n"
        "  }\n"
        "  const prompt = document.querySelector(\n"
        '    \'[aria-label="Prompt"][contenteditable="true"][data-sidebar-file-drop-editor-agent-id="\' + agentId + \'"]\'\n'
        "  );\n"
        "  const hasDomEditor = prompt && prompt.editor && prompt.editor.commands;\n"
        "  const stale = /^BRIDGE_WAKE\\s+thread=\\S+\\s+turn=\\d+(?:,\\d+)*$/.test(String(text || '').trim());\n"
        "  if (hasDomEditor) {\n"
        "    const existing = (prompt.innerText || '').trim();\n"
        "    if (existing.length > 0 && !stale && existing !== String(text || '').trim()) {\n"
        "      return {ok: false, reason: 'composer_not_empty', draft: existing, agent_id: agentId};\n"
        "    }\n"
        "  }\n"
        "  const data = handle.data || {};\n"
        "  const status = data.status || '';\n"
        "  const composerUtilsService = byId.get('composerUtilsService');\n"
        "  const clearDraft = () => {\n"
        "    try {\n"
        "      if (composerUtilsService && handle) composerUtilsService.clearText(handle);\n"
        "    } catch (_) {}\n"
        "    try {\n"
        "      if (prompt?.editor?.commands?.clearContent) prompt.editor.commands.clearContent();\n"
        "    } catch (_) {}\n"
        "  };\n"
        "  let resolveDispatched;\n"
        "  const dispatchedPromise = new Promise((r) => { resolveDispatched = r; });\n"
        "  const submitOpts = {\n"
        "    richText: text,\n"
        "    skipFocusAfterSubmission: true,\n"
        "    _internalOnMessageRendered: () => { clearDraft(); resolveDispatched('rendered'); },\n"
        "    onDidSendRequest: () => { clearDraft(); resolveDispatched('sent'); },\n"
        "  };\n"
        "  if (status === 'generating') {\n"
        "    submitOpts.steerIntent = true;\n"
        "    submitOpts.ignoreQueuing = true;\n"
        "  }\n"
        "  const submitCall = Promise.resolve(\n"
        "    composerChatService.submitChatMaybeAbortCurrent(agentId, text, submitOpts)\n"
        "  ).then(() => 'completed');\n"
        "  const timer = new Promise((r) => setTimeout(() => r('timer'), 500));\n"
        "  let raceWinner;\n"
        "  try {\n"
        "    raceWinner = await Promise.race([submitCall, dispatchedPromise, timer]);\n"
        "  } finally {\n"
        "    clearDraft();\n"
        "  }\n"
        "  const delivered = raceWinner !== 'timer';\n"
        "  return {ok: delivered, delivered, race_winner: raceWinner, agent_id: agentId,\n"
        "    editor_agent_id: agentId, transport: 'cdp_glass_services',\n"
        "    reason: delivered ? 'submitted' : 'delivery_unconfirmed'};\n"
        "  };\n"
        "  window.__ulgPendingLaunchPromise = task();\n"
        "  try {\n"
        "    return await window.__ulgPendingLaunchPromise;\n"
        "  } finally {\n"
        "    window.__ulgPendingLaunchPromise = null;\n"
        "  }\n"
        "})()"
    )


def probe_glass_composer_budget_js(transcript_id: str) -> str:
    """CDP expression: read live composer context budget fields for one tab."""
    tid = json.dumps(str(transcript_id or "").strip())
    return (
        "(async () => {\n"
        f"{GLASS_SERVICE_MAP_FN}\n"
        f"{RESOLVE_WORKSPACE_SERVICES_FN}\n"
        f"  const agentId = {tid};\n"
        "  const anchor = document.querySelector('[data-perf-id=\"glass-sidebar-agent-row\"]')"
        ' || document.querySelector(\'[aria-label="Prompt"][contenteditable="true"]\');\n'
        "  if (!anchor) return {ok: false, reason: 'no_anchor'};\n"
        "  const byId = glassServiceMap(anchor);\n"
        "  if (!byId) return {ok: false, reason: 'no_services'};\n"
        "  const { services } = resolveWorkspaceServices(byId, agentId);\n"
        "  const composerDataService = services.get('composerDataService');\n"
        "  if (!composerDataService) return {ok: false, reason: 'missing_services'};\n"
        "  const handle = await composerDataService.getComposerHandleById(agentId);\n"
        "  if (!handle) return {ok: false, reason: 'no_handle'};\n"
        "  const data = handle.data || {};\n"
        "  let summarized = 0;\n"
        "  const cats = (((data.promptTokenBreakdown || {}).categories) || []);\n"
        "  for (const cat of cats) {\n"
        "    if (cat && cat.id === 'summarized_conversation') {\n"
        "      summarized = Number(cat.estimatedTokens || 0) || 0;\n"
        "    }\n"
        "  }\n"
        "  const headers = data.fullConversationHeadersOnly || [];\n"
        "  return {\n"
        "    ok: true,\n"
        "    contextTokensUsed: Number(data.contextTokensUsed || 0) || 0,\n"
        "    contextTokenLimit: Number(data.contextTokenLimit || 0) || 0,\n"
        "    contextUsagePercent: Number(data.contextUsagePercent || 0) || 0,\n"
        "    status: String(data.status || ''),\n"
        "    model: String((data.modelConfig && data.modelConfig.modelName) || ''),\n"
        "    glass_summarized_tokens: summarized,\n"
        "    glass_headers_count: Array.isArray(headers) ? headers.length : 0,\n"
        "  };\n"
        "})()"
    )


def submit_wake_line_js(line: str, agent_id: str = "") -> str:
    """CDP expression: submit a wake line via Glass chat service only, then clear draft."""
    text = json.dumps(line)
    aid = json.dumps(str(agent_id or "").strip())
    return (
        "(async () => {\n"
        f"{GLASS_SERVICE_MAP_FN}\n"
        f"{RESOLVE_WORKSPACE_SERVICES_FN}\n"
        f"  const text = {text};\n"
        f"  const scopedId = {aid};\n"
        "  let agentId = scopedId;\n"
        "  if (!agentId) {\n"
        '    const activeRow = document.querySelector(\'[data-perf-id="glass-sidebar-agent-row"][data-active="true"]\')\n'
        "      || document.querySelector('.ui-sidebar-menu-button-trigger[aria-current=\"page\"]')\n"
        "        ?.closest('[data-perf-id=\"glass-sidebar-agent-row\"]');\n"
        "    if (activeRow) {\n"
        "      agentId = (activeRow.getAttribute('data-sidebar-file-drop-agent-id')\n"
        "        || activeRow.getAttribute('data-agent-id') || '').trim();\n"
        "    }\n"
        "  }\n"
        "  const promptSel = agentId\n"
        '    ? \'[aria-label="Prompt"][contenteditable="true"][data-sidebar-file-drop-editor-agent-id="\' + agentId + \'"]\'\n'
        '    : \'[aria-label="Prompt"][contenteditable="true"]\';\n'
        "  const prompt = document.querySelector(promptSel);\n"
        "  const anchor = document.querySelector('[data-perf-id=\"glass-sidebar-agent-row\"]') || prompt;\n"
        "  if (!anchor) return {ok: false, reason: 'no_anchor'};\n"
        "  const byId = glassServiceMap(anchor);\n"
        "  if (!byId) return {ok: false, reason: 'no_services'};\n"
        "  if (!agentId && prompt) {\n"
        "    agentId = (prompt.getAttribute('data-sidebar-file-drop-editor-agent-id') || '').trim();\n"
        "  }\n"
        "  const { services } = resolveWorkspaceServices(byId, agentId);\n"
        "  const composerDataService = services.get('composerDataService');\n"
        "  const composerChatService = services.get('composerChatService');\n"
        "  const composerUtilsService = services.get('composerUtilsService');\n"
        "  if (!composerDataService || !composerChatService) {\n"
        "    return {ok: false, reason: 'missing_services'};\n"
        "  }\n"
        "  if (!agentId) return {ok: false, reason: 'no_agent_id'};\n"
        "  const handle = await composerDataService.getComposerHandleById(agentId);\n"
        "  if (!handle) return {ok: false, reason: 'no_handle'};\n"
        "  const status = (handle.data && handle.data.status) || '';\n"
        "  const clearDraft = () => {\n"
        "    try {\n"
        "      if (composerUtilsService && handle) composerUtilsService.clearText(handle);\n"
        "    } catch (_) {}\n"
        "    try {\n"
        "      if (prompt?.editor?.commands?.clearContent) prompt.editor.commands.clearContent();\n"
        "    } catch (_) {}\n"
        "  };\n"
        "  let resolveDispatched;\n"
        "  const dispatchedPromise = new Promise((r) => { resolveDispatched = r; });\n"
        "  const submitOpts = {\n"
        "    richText: text,\n"
        "    skipFocusAfterSubmission: true,\n"
        "    _internalOnMessageRendered: () => { clearDraft(); resolveDispatched('rendered'); },\n"
        "    onDidSendRequest: () => { clearDraft(); resolveDispatched('sent'); },\n"
        "  };\n"
        "  let submitCall;\n"
        "  if (status === 'generating') {\n"
        "    submitCall = Promise.resolve(composerChatService.submitChatMaybeAbortCurrent(agentId, text, {\n"
        "      ...submitOpts,\n"
        "      steerIntent: true,\n"
        "      ignoreQueuing: true,\n"
        "    })).then(() => 'completed');\n"
        "  } else {\n"
        "    submitCall = Promise.resolve(\n"
        "      composerChatService.submitChatMaybeAbortCurrent(agentId, text, submitOpts)\n"
        "    ).then(() => 'completed');\n"
        "  }\n"
        "  const timer = new Promise((r) => setTimeout(() => r('timer'), 500));\n"
        "  let raceWinner;\n"
        "  try {\n"
        "    raceWinner = await Promise.race([submitCall, dispatchedPromise, timer]);\n"
        "    const delivered = raceWinner !== 'timer';\n"
        "    return {ok: delivered, delivered, race_winner: raceWinner, agent_id: agentId,\n"
        "      reason: delivered ? 'submitted' : 'delivery_unconfirmed'};\n"
        "  } finally {\n"
        "    clearDraft();\n"
        "    try {\n"
        "      if (composerUtilsService && handle) composerUtilsService.clearText(handle);\n"
        "    } catch (_) {}\n"
        "    try {\n"
        "      if (prompt?.editor?.commands?.clearContent) prompt.editor.commands.clearContent();\n"
        "    } catch (_) {}\n"
        "  }\n"
        "})()"
    )


def rename_composer_title_js(agent_id: str, title: str) -> str:
    """Persist sidebar title through Glass composer services."""
    aid = json.dumps(agent_id.strip())
    new_title = json.dumps(title.strip())
    return (
        "(async () => {\n"
        f"{GLASS_SERVICE_MAP_FN}\n"
        f"{RESOLVE_WORKSPACE_SERVICES_FN}\n"
        f"  const target = {aid};\n"
        f"  const newTitle = {new_title};\n"
        "  const row = Array.from(document.querySelectorAll('[data-perf-id=\"glass-sidebar-agent-row\"]'))\n"
        "    .find((el) => (el.getAttribute('data-sidebar-file-drop-agent-id')\n"
        "      || el.getAttribute('data-agent-id') || '').trim() === target);\n"
        "  const anchor = row || document.querySelector('[data-perf-id=\"glass-sidebar-agent-row\"]');\n"
        "  if (!anchor) return {ok: false, reason: 'row_not_found'};\n"
        "  const byId = glassServiceMap(anchor);\n"
        "  if (!byId) return {ok: false, reason: 'no_services'};\n"
        "  const { services } = resolveWorkspaceServices(byId, target);\n"
        "  const composerService = services.get('composerService');\n"
        "  const composerDataService = services.get('composerDataService');\n"
        "  if (!composerService || !composerDataService) {\n"
        "    return {ok: false, reason: 'missing_services'};\n"
        "  }\n"
        "  const handle = await composerDataService.getComposerHandleById(target);\n"
        "  if (!handle) return {ok: false, reason: 'no_handle'};\n"
        "  const labelEl = row && row.querySelector('[class*=\"agent-item__title\"]');\n"
        "  const prevTitle = labelEl ? (labelEl.textContent || '').trim() : '';\n"
        "  handle.setData('name', newTitle);\n"
        "  composerDataService.setAllComposersData(\n"
        "    'allComposers',\n"
        "    (rowData) => rowData.composerId === target,\n"
        "    { name: newTitle },\n"
        "  );\n"
        "  await composerService.renameComposer(target, newTitle, { throwOnFailure: true });\n"
        "  await composerDataService.manuallyPersistComposerOrThrow(target);\n"
        "  await composerDataService.saveComposers();\n"
        "  return {ok: true, id: target, previous_title: prevTitle, new_title: newTitle,\n"
        "    persist_path: 'glass_services'};\n"
        "})()"
    )

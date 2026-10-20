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


def _resolve_catalog_js() -> str:
    return r"""
async function resolveCatalog(modelConfigService, label) {
  const q = String(label || '').trim();
  if (!q) return null;
  if (modelConfigService && typeof modelConfigService.resolveModelNameToCatalog === 'function') {
    try {
      const cat = await modelConfigService.resolveModelNameToCatalog(q);
      if (cat && (cat.modelId || cat.modelName)) {
        return {
          modelId: cat.modelId || cat.modelName,
          parameters: cat.parameters || [],
        };
      }
    } catch (e) { /* fall through */ }
  }
  const lower = q.toLowerCase();
  if (lower.includes('grok') && lower.includes('4.7')) {
    const params = [
      { id: 'context', value: '256k' },
      { id: 'reasoning_effort', value: 'xhigh' },
      { id: 'fast', value: 'false' },
    ];
    return { modelId: 'grok-4.7', parameters: params };
  }
  return { modelId: q, parameters: [] };
}
"""


def launch_new_agent_tab_js(message: str, model_query: str) -> str:
    """CDP expression: create agent tab, set model, paste ``message``, submit."""
    msg = json.dumps(message)
    mq = json.dumps(str(model_query or "").strip())
    return (
        "(async () => {\n"
        f"{GLASS_SERVICE_MAP_FN}\n"
        f"{_resolve_catalog_js()}\n"
        "  const text = "
        f"{msg};\n"
        "  const modelLabel = "
        f"{mq};\n"
        "  const anchor = document.querySelector('[data-perf-id=\"glass-sidebar-agent-row\"]')"
        " || document.querySelector('[aria-label=\"Prompt\"][contenteditable=\"true\"]');\n"
        "  if (!anchor) return {ok: false, reason: 'no_anchor'};\n"
        "  const byId = glassServiceMap(anchor);\n"
        "  if (!byId) return {ok: false, reason: 'no_services'};\n"
        "  const composerService = byId.get('composerService');\n"
        "  const composerDataService = byId.get('composerDataService');\n"
        "  const modelConfigService = byId.get('modelConfigService');\n"
        "  const composerChatService = byId.get('composerChatService');\n"
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
        "    const cat = await resolveCatalog(modelConfigService, modelLabel);\n"
        "    if (cat && cat.modelId) {\n"
        "      modelConfigService.setModelConfigForComposer(handle, {\n"
        "        modelName: cat.modelId,\n"
        "        maxMode: false,\n"
        "        selectedModels: [{ modelId: cat.modelId, parameters: cat.parameters || [] }],\n"
        "      }, 'composer');\n"
        "    }\n"
        "  }\n"
        "  const prompt = document.querySelector(\n"
        "    '[aria-label=\"Prompt\"][contenteditable=\"true\"][data-sidebar-file-drop-editor-agent-id=\"' + agentId + '\"]'\n"
        "  );\n"
        "  if (!prompt || !prompt.editor || !prompt.editor.commands) {\n"
        "    return {ok: false, reason: 'no_composer', agent_id: agentId};\n"
        "  }\n"
        "  const stale = /^BRIDGE_WAKE\\s+thread=\\S+\\s+turn=\\d+(?:,\\d+)*$/.test(String(text || '').trim());\n"
        "  const existing = (prompt.innerText || '').trim();\n"
        "  if (existing.length > 0 && !stale && existing !== String(text || '').trim()) {\n"
        "    return {ok: false, reason: 'composer_not_empty', draft: existing, agent_id: agentId};\n"
        "  }\n"
        "  prompt.editor.commands.setContent(text);\n"
        "  composerDataService.updateComposerData(handle, { text, richText: text });\n"
        "  const data = handle.data || {};\n"
        "  const status = data.status || '';\n"
        "  if (status === 'generating') {\n"
        "    composerChatService.appendQueuedHumanMessage(handle, text, {\n"
        "      steerIntent: true,\n"
        "      richText: text,\n"
        "    });\n"
        "    await composerChatService.submitChatMaybeAbortCurrent(agentId, text, {\n"
        "      steerIntent: true,\n"
        "      skipFocusAfterSubmission: true,\n"
        "      skipClearInput: true,\n"
        "      ignoreQueuing: true,\n"
        "    });\n"
        "  } else {\n"
        "    await composerChatService.submitChatMaybeAbortCurrent(agentId, text, {\n"
        "      skipFocusAfterSubmission: true,\n"
        "    });\n"
        "  }\n"
        "  return {ok: true, agent_id: agentId, editor_agent_id: agentId, transport: 'cdp_glass_services'};\n"
        "})()"
    )


def submit_wake_line_js(line: str, agent_id: str = "") -> str:
    """CDP expression: paste a wake line into the active or scoped composer."""
    text = json.dumps(line)
    aid = json.dumps(str(agent_id or "").strip())
    return (
        "(async () => {\n"
        f"{GLASS_SERVICE_MAP_FN}\n"
        f"  const text = {text};\n"
        f"  const scopedId = {aid};\n"
        "  let agentId = scopedId;\n"
        "  if (!agentId) {\n"
        "    const activeRow = document.querySelector('[data-perf-id=\"glass-sidebar-agent-row\"][data-active=\"true\"]')\n"
        "      || document.querySelector('.ui-sidebar-menu-button-trigger[aria-current=\"page\"]')\n"
        "        ?.closest('[data-perf-id=\"glass-sidebar-agent-row\"]');\n"
        "    if (activeRow) {\n"
        "      agentId = (activeRow.getAttribute('data-sidebar-file-drop-agent-id')\n"
        "        || activeRow.getAttribute('data-agent-id') || '').trim();\n"
        "    }\n"
        "  }\n"
        "  const promptSel = agentId\n"
        "    ? '[aria-label=\"Prompt\"][contenteditable=\"true\"][data-sidebar-file-drop-editor-agent-id=\"' + agentId + '\"]'\n"
        "    : '[aria-label=\"Prompt\"][contenteditable=\"true\"]';\n"
        "  const prompt = document.querySelector(promptSel);\n"
        "  if (!prompt) return {ok: false, reason: 'no_composer'};\n"
        "  const byId = glassServiceMap(prompt);\n"
        "  if (!byId) return {ok: false, reason: 'no_services'};\n"
        "  const composerDataService = byId.get('composerDataService');\n"
        "  const composerChatService = byId.get('composerChatService');\n"
        "  if (!composerDataService || !composerChatService) {\n"
        "    return {ok: false, reason: 'missing_services'};\n"
        "  }\n"
        "  if (!agentId) {\n"
        "    agentId = (prompt.getAttribute('data-sidebar-file-drop-editor-agent-id') || '').trim();\n"
        "  }\n"
        "  if (!agentId) return {ok: false, reason: 'no_agent_id'};\n"
        "  const handle = await composerDataService.getComposerHandleById(agentId);\n"
        "  if (!handle) return {ok: false, reason: 'no_handle'};\n"
        "  if (!prompt.editor || !prompt.editor.commands) {\n"
        "    return {ok: false, reason: 'no_editor'};\n"
        "  }\n"
        "  prompt.editor.commands.setContent(text);\n"
        "  composerDataService.updateComposerData(handle, { text, richText: text });\n"
        "  const status = (handle.data && handle.data.status) || '';\n"
        "  if (status === 'generating') {\n"
        "    composerChatService.appendQueuedHumanMessage(handle, text, {\n"
        "      steerIntent: true,\n"
        "      richText: text,\n"
        "    });\n"
        "    await composerChatService.submitChatMaybeAbortCurrent(agentId, text, {\n"
        "      steerIntent: true,\n"
        "      skipFocusAfterSubmission: true,\n"
        "      skipClearInput: true,\n"
        "      ignoreQueuing: true,\n"
        "    });\n"
        "  } else {\n"
        "    await composerChatService.submitChatMaybeAbortCurrent(agentId, text, {\n"
        "      skipFocusAfterSubmission: true,\n"
        "    });\n"
        "  }\n"
        "  return {ok: true, agent_id: agentId};\n"
        "})()"
    )


def rename_composer_title_js(agent_id: str, title: str) -> str:
    """Persist sidebar title through Glass composer services."""
    aid = json.dumps(agent_id.strip())
    new_title = json.dumps(title.strip())
    return (
        "(async () => {\n"
        f"{GLASS_SERVICE_MAP_FN}\n"
        f"  const target = {aid};\n"
        f"  const newTitle = {new_title};\n"
        "  const row = Array.from(document.querySelectorAll('[data-perf-id=\"glass-sidebar-agent-row\"]'))\n"
        "    .find((el) => (el.getAttribute('data-sidebar-file-drop-agent-id')\n"
        "      || el.getAttribute('data-agent-id') || '').trim() === target);\n"
        "  const anchor = row || document.querySelector('[data-perf-id=\"glass-sidebar-agent-row\"]');\n"
        "  if (!anchor) return {ok: false, reason: 'row_not_found'};\n"
        "  const byId = glassServiceMap(anchor);\n"
        "  if (!byId) return {ok: false, reason: 'no_services'};\n"
        "  const composerService = byId.get('composerService');\n"
        "  const composerDataService = byId.get('composerDataService');\n"
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

// Cortex Workbench v2.2.0 — React JSX, dual transport (sandbox + standalone)
// Sandbox (claude.ai):  Anthropic API → MCP tools for data, Anthropic API direct for AI
// Standalone (Vite):    REST to mcp.k-1.me/cortex-api + /llm/v1/messages
//
// Model tiering (cost optimization):
//   HAIKU  — MCP data ops, structured extraction (12x cheaper than Sonnet)
//   SONNET — Description generation, nuanced writing

import { useState, useEffect, useCallback, useRef } from "react";

const CORTEX_API = "https://mcp.k-1.me/cortex-api";
const LLM_API = "https://mcp.k-1.me/llm/v1/messages";
const ANTHROPIC_API = "https://api.anthropic.com/v1/messages";
const MCP_SERVER = { type: "url", url: "https://mcp.k-1.me/mcp", name: "vortex" };

// Model tiering — cheap models for structured tasks, capable models for nuance
const MODELS = {
  haiku: "claude-haiku-4-5-20251001",   // MCP ops, JSON extraction
  sonnet: "claude-sonnet-4-20250514",   // Description generation
};

const TYPE_META = {
  person: { icon: "👤", color: "text-blue-400", bg: "bg-blue-400/10" },
  organization: { icon: "🏢", color: "text-purple-400", bg: "bg-purple-400/10" },
  legal_matter: { icon: "⚖️", color: "text-red-400", bg: "bg-red-400/10" },
  event: { icon: "📅", color: "text-green-400", bg: "bg-green-400/10" },
  decision: { icon: "✅", color: "text-emerald-400", bg: "bg-emerald-400/10" },
  document: { icon: "📄", color: "text-slate-400", bg: "bg-slate-400/10" },
  deadline: { icon: "⏰", color: "text-orange-400", bg: "bg-orange-400/10" },
  property: { icon: "🏠", color: "text-amber-400", bg: "bg-amber-400/10" },
  discovery: { icon: "🔍", color: "text-cyan-400", bg: "bg-cyan-400/10" },
};

const CONF_COLORS = {
  confirmed: "text-emerald-400 bg-emerald-400/15 border-emerald-400/30",
  believed: "text-blue-400 bg-blue-400/15 border-blue-400/30",
  suspected: "text-amber-400 bg-amber-400/15 border-amber-400/30",
  hypothesized: "text-rose-400 bg-rose-400/15 border-rose-400/30",
};

let _bearerToken = "";
let _mode = null; // "sandbox" | "standalone"
let _detectPromise = null;

async function detectMode() {
  if (_mode) return _mode;
  if (_detectPromise) return _detectPromise;
  
  _detectPromise = (async () => {
    // URL param override for testing
    const params = new URLSearchParams(window.location.search);
    if (params.get("mode") === "sandbox") { _mode = "sandbox"; return _mode; }
    if (params.get("mode") === "standalone") { _mode = "standalone"; return _mode; }
    
    // Primary detection: try Anthropic API without auth
    // In claude.ai artifacts, this succeeds (env provides auth)
    // Outside artifacts, this fails with 401
    try {
      const res = await fetch(ANTHROPIC_API, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: "claude-haiku-4-5-20251001", // Haiku for ping — cheapest
          max_tokens: 1,
          messages: [{ role: "user", content: "ping" }],
        }),
      });
      // 200 or 529 (overloaded) = API is reachable with implicit auth = sandbox
      // 401 = no auth = standalone
      _mode = res.status === 401 ? "standalone" : "sandbox";
    } catch (e) {
      // Network error or CORS block → fallback to health check
      try {
        await fetch(`${CORTEX_API}/health`, { method: "HEAD", mode: "cors" });
        _mode = "standalone";
      } catch {
        _mode = "sandbox";
      }
    }
    return _mode;
  })();
  
  return _detectPromise;
}

function getMode() {
  return _mode || "sandbox"; // default to sandbox if detection hasn't run
}

function authHeaders() {
  return { Authorization: `Bearer ${_bearerToken}`, "Content-Type": "application/json" };
}

// ===== MCP Transport (sandbox mode) =====

const MCP_PROMPTS = {
  cortex_entities: (args) =>
    `Call the cortex_entities tool with limit=${args.limit || 200}. Return ONLY the raw JSON tool result, no commentary.`,
  cortex_entity_get: (args) =>
    `Call the cortex_entity_get tool with entity_id="${args.entity_id}". Return ONLY the raw JSON tool result, no commentary.`,
  cortex_assert: (args) =>
    `Call the cortex_assert tool with entity_id="${args.entity_id}", claim="${args.claim}", confidence="${args.confidence}", evidence="${args.evidence}"${args.evidence_uris ? `, evidence_uris="${Array.isArray(args.evidence_uris) ? args.evidence_uris.join(",") : args.evidence_uris}"` : ""}. Return confirmation.`,
  sqlite_execute: (args) =>
    `Call dispatch with tool="sqlite_execute" and arguments='${JSON.stringify({ db: "cortex", statement: args.statement, params: args.params })}'. Return the result.`,
};

function extractMCPResult(data) {
  if (data.error) throw new Error(`Anthropic API error: ${data.error.message}`);

  const errorBlocks = (data.content || []).filter((item) => item.type === "mcp_tool_result" && item.is_error);
  if (errorBlocks.length > 0) {
    const errorText = errorBlocks.map((b) => b.content?.[0]?.text).join("; ");
    throw new Error(`MCP tool error: ${errorText}`);
  }

  const toolResults = (data.content || [])
    .filter((item) => item.type === "mcp_tool_result")
    .map((item) => {
      const text = item.content?.[0]?.text;
      if (!text) return null;
      try { return JSON.parse(text); } catch { return text; }
    })
    .filter(Boolean);

  if (toolResults.length === 0) {
    const textBlocks = (data.content || []).filter((item) => item.type === "text").map((item) => item.text);
    return { raw: textBlocks.join("\n") };
  }
  return toolResults.length === 1 ? toolResults[0] : toolResults;
}

async function mcpCall(toolName, args) {
  const promptFn = MCP_PROMPTS[toolName];
  const prompt = promptFn ? promptFn(args) : `Call ${toolName} with ${JSON.stringify(args)}. Return the raw result.`;

  const res = await fetch(ANTHROPIC_API, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model: MODELS.haiku, // Haiku for MCP ops — structured tool calls
      max_tokens: 4096,
      messages: [{ role: "user", content: prompt }],
      mcp_servers: [MCP_SERVER],
    }),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`Anthropic API ${res.status}: ${text.slice(0, 200)}`);
  }
  const data = await res.json();
  return extractMCPResult(data);
}

// ===== Unified Data Layer =====

async function cortexGetEntities(limit = 200) {
  if (getMode() === "sandbox") return mcpCall("cortex_entities", { limit });
  const res = await fetch(`${CORTEX_API}/entities?limit=${limit}`, { headers: authHeaders() });
  if (!res.ok) throw new Error(`cortex-api ${res.status}: ${(await res.text()).slice(0, 200)}`);
  return res.json();
}

async function cortexGetEntity(entityId) {
  if (getMode() === "sandbox") return mcpCall("cortex_entity_get", { entity_id: entityId });
  const res = await fetch(`${CORTEX_API}/entities/${entityId}`, { headers: authHeaders() });
  if (!res.ok) throw new Error(`cortex-api ${res.status}: ${(await res.text()).slice(0, 200)}`);
  return res.json();
}

async function cortexUpdateEntity(entityId, notes) {
  if (getMode() === "sandbox") {
    return mcpCall("sqlite_execute", {
      statement: "UPDATE entities SET notes = ?, updated_at = datetime('now') WHERE id = ?",
      params: [notes, entityId],
    });
  }
  const res = await fetch(`${CORTEX_API}/entities/${entityId}`, {
    method: "PATCH", headers: authHeaders(), body: JSON.stringify({ notes }),
  });
  if (!res.ok) throw new Error(`cortex-api PATCH ${res.status}: ${(await res.text()).slice(0, 200)}`);
  return res.json();
}

async function cortexCreateAssertion(assertion) {
  if (getMode() === "sandbox") return mcpCall("cortex_assert", assertion);
  const res = await fetch(`${CORTEX_API}/assertions`, {
    method: "POST", headers: authHeaders(), body: JSON.stringify(assertion),
  });
  if (!res.ok) throw new Error(`cortex-api POST ${res.status}: ${(await res.text()).slice(0, 200)}`);
  return res.json();
}

// ===== Unified AI Layer =====

async function callLLM(messages, { system, model, max_tokens } = {}) {
  const payload = {
    model: model || MODELS.sonnet, // Default to Sonnet for quality
    max_tokens: max_tokens || 4096,
    messages,
  };
  if (system) payload.system = system;

  if (getMode() === "sandbox") {
    payload.mcp_servers = [MCP_SERVER];
    const res = await fetch(ANTHROPIC_API, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(`Anthropic API ${res.status}: ${(await res.text()).slice(0, 200)}`);
    return res.json();
  }

  if (!_bearerToken) throw new Error("Vortex token not set — open Settings");
  const res = await fetch(LLM_API, { method: "POST", headers: authHeaders(), body: JSON.stringify(payload) });
  if (!res.ok) throw new Error(`LLM proxy ${res.status}: ${(await res.text()).slice(0, 200)}`);
  return res.json();
}

function extractText(data) {
  return (data.content || []).filter((b) => b.type === "text").map((b) => b.text).join("\n");
}

function Spinner({ size = "w-4 h-4" }) {
  return <div className={`${size} border-2 border-amber-400/30 border-t-amber-400 rounded-full animate-spin`} />;
}

function Badge({ children, className = "" }) {
  return <span className={`px-2 py-0.5 text-xs font-mono rounded border ${className}`}>{children}</span>;
}

function TabButton({ active, onClick, children }) {
  return (
    <button onClick={onClick} className={`px-4 py-2 text-sm font-medium transition-all border-b-2 ${active ? "border-amber-400 text-amber-400" : "border-transparent text-slate-500 hover:text-slate-300 hover:border-slate-600"}`}>
      {children}
    </button>
  );
}

function EntityListItem({ entity, selected, onClick }) {
  const meta = TYPE_META[entity.type] || { icon: "•", color: "text-slate-400" };
  return (
    <button onClick={onClick} className={`w-full text-left px-3 py-2 flex items-center gap-2 transition-all rounded-md text-sm ${selected ? "bg-amber-400/10 text-amber-300 ring-1 ring-amber-400/30" : "hover:bg-slate-800/60 text-slate-300"}`}>
      <span className="text-base flex-shrink-0">{meta.icon}</span>
      <span className="truncate">{entity.name}</span>
    </button>
  );
}

function AssertionCard({ assertion }) {
  const confClass = CONF_COLORS[assertion.confidence] || CONF_COLORS.believed;
  return (
    <div className="bg-slate-800/40 border border-slate-700/50 rounded-lg p-3 space-y-2">
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm text-slate-200 leading-relaxed flex-1">{assertion.claim}</p>
        <Badge className={confClass}>{assertion.confidence}</Badge>
      </div>
      {assertion.evidence && <p className="text-xs text-slate-500 leading-relaxed"><span className="text-slate-600 font-medium">Evidence:</span> {assertion.evidence}</p>}
      {assertion.evidence_uris && <p className="text-xs text-slate-600 font-mono">{Array.isArray(assertion.evidence_uris) ? assertion.evidence_uris.join(", ") : assertion.evidence_uris}</p>}
    </div>
  );
}

function tryParseJSON(text) {
  const clean = text.replace(/```json\s*/g, "").replace(/```/g, "").trim();
  try { return JSON.parse(clean); } catch { return null; }
}

export default function CortexWorkbench({ initialToken = "" } = {}) {
  const [entities, setEntities] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [tab, setTab] = useState("entities");
  const [loading, setLoading] = useState({});
  const [proposedDesc, setProposedDesc] = useState(null);
  const [ingestText, setIngestText] = useState("");
  const [extraction, setExtraction] = useState(null);
  const [log, setLog] = useState([]);
  const [error, setError] = useState(null);
  const [typeFilter, setTypeFilter] = useState(null);
  const [committed, setCommitted] = useState([]);
  const [showSettings, setShowSettings] = useState(false);
  const [token, setToken] = useState("");
  const [tokenReady, setTokenReady] = useState(false);
  const logRef = useRef(null);
  const mode = getMode();
  const isSandbox = mode === "sandbox";

  const addLog = useCallback((msg, type = "info") => {
    const entry = { ts: new Date().toLocaleTimeString(), msg, type };
    setLog((prev) => [...prev.slice(-50), entry]);
  }, []);

  const setLoadingKey = (key, val) => setLoading((prev) => ({ ...prev, [key]: val }));

  useEffect(() => {
    (async () => {
      await detectMode();
      const mode = getMode();
      if (mode === "sandbox") {
        setTokenReady(true);
        return;
      }
      try {
        const saved = localStorage.getItem("cortex_wb_token") || "";
        const effective = initialToken || saved;
        if (effective) { setToken(effective); _bearerToken = effective; }
      } catch {}
      setTokenReady(true);
    })();
  }, []);

  const loadEntities = useCallback(async () => {
    if (!isSandbox && !_bearerToken) { setShowSettings(true); return; }
    setLoadingKey("entities", true);
    setError(null);
    addLog(isSandbox ? "Haiku is fetching entities via MCP..." : "Loading entities...");
    try {
      const data = await cortexGetEntities(200);
      const items = data.items || [];
      setEntities(items);
      addLog(`Loaded ${items.length} entities`, "success");
    } catch (e) { setError(e.message); addLog("Error: " + e.message, "error"); }
    setLoadingKey("entities", false);
  }, [addLog, isSandbox]);

  useEffect(() => {
    if (tokenReady && (isSandbox || _bearerToken)) loadEntities();
    else if (tokenReady && !isSandbox && !_bearerToken) setShowSettings(true);
  }, [tokenReady, loadEntities, isSandbox]);

  const saveToken = useCallback((t) => {
    const trimmed = t.trim();
    setToken(trimmed);
    _bearerToken = trimmed;
    try { localStorage.setItem("cortex_wb_token", trimmed); } catch {}
    if (trimmed) loadEntities();
  }, [loadEntities]);

  const loadDetail = useCallback(async (entityId) => {
    setSelectedId(entityId); setDetail(null); setProposedDesc(null); setLoadingKey("detail", true);
    addLog(isSandbox ? `Haiku is retrieving ${entityId} via MCP...` : `Loading ${entityId}...`);
    try {
      const entityData = await cortexGetEntity(entityId);
      setDetail(entityData);
      addLog(`Loaded ${entityData.name || entityId}`, "success");
    } catch (e) { addLog("Error loading detail: " + e.message, "error"); }
    setLoadingKey("detail", false);
  }, [addLog, isSandbox]);

  const generateDescription = useCallback(async () => {
    if (!detail) return;
    setLoadingKey("description", true); setProposedDesc(null);
    addLog(`Sonnet generating description for ${detail.name}...`);
    try {
      const assertionsSummary = (detail.assertions || []).map((a) => `- [${a.confidence}] ${a.claim}`).join("\n");
      const entityList = entities.filter((e) => e.type === detail.type && e.id !== detail.id).map((e) => `  ${e.id}: ${e.name}`).join("\n");
      const data = await callLLM(
        [{ role: "user", content: `Entity: ${detail.name} (${detail.id})\nType: ${detail.type}\n${detail.notes ? `Current notes: ${detail.notes}\n` : ""}\nAssertions:\n${assertionsSummary || "(none)"}\n\nSimilar entities to distinguish from:\n${entityList || "(none)"}` }],
        {
          system: `You are writing entity descriptions for a personal knowledge graph belonging to Kaywan Joseph Mansubi, a PharmD pursuing a legal case involving his parents' estate. Write 2-4 sentences that: 1) State who/what this entity is, 2) Describe their role or relationship to Kaywan, 3) Distinguish from similar entities of the same type. Be specific, factual, and contrastive. Respond with ONLY the description text, nothing else.`,
          max_tokens: 1000,
        }
      );
      const desc = extractText(data).trim();
      setProposedDesc(desc); addLog("Description generated", "success");
    } catch (e) { addLog("Error generating description: " + e.message, "error"); }
    setLoadingKey("description", false);
  }, [detail, entities, addLog]);

  const commitDescription = useCallback(async (desc) => {
    if (!detail) return;
    setLoadingKey("commitDesc", true);
    addLog(isSandbox ? `Haiku committing description via MCP...` : `Committing description for ${detail.name}...`);
    try {
      const updated = await cortexUpdateEntity(detail.id, desc);
      if (isSandbox) {
        setDetail((prev) => prev ? { ...prev, notes: desc } : prev);
      } else {
        setDetail(updated);
      }
      setProposedDesc(null);
      addLog(`Description committed for ${detail.name}`, "success");
    } catch (e) { addLog("Error committing: " + e.message, "error"); }
    setLoadingKey("commitDesc", false);
  }, [detail, addLog, isSandbox]);

  const extractKnowledge = useCallback(async () => {
    if (!ingestText.trim()) return;
    setLoadingKey("extract", true); setExtraction(null);
    addLog("Haiku extracting knowledge from text...");
    try {
      const entityContext = entities.map((e) => `${e.id}: ${e.name} (${e.type})`).join("\n");
      const data = await callLLM(
        [{ role: "user", content: `EXISTING ENTITIES:\n${entityContext}\n\nTEXT TO EXTRACT FROM:\n${ingestText}` }],
        {
          model: MODELS.haiku, // Haiku for structured JSON extraction
          system: `You are a knowledge extraction system for a personal knowledge graph. Given text, extract structured assertions.\n\nRULES:\n- Each assertion must be atomic (one fact), faithful (accurate to source), and decontextualized (no pronouns — use full names).\n- Resolve mentions to existing entities when possible. Use the entity ID format type:slug.\n- Confidence levels: confirmed (verified fact), believed (high confidence), suspected (pattern-based), hypothesized (theory).\n- Include reasoning for each assertion.\n\nRespond with ONLY a JSON object in this format:\n{\n  "assertions": [\n    {\n      "entity_id": "type:slug",\n      "entity_name": "Display Name",\n      "claim": "The atomic assertion",\n      "confidence": "believed",\n      "evidence": "Why we believe this — 1 sentence",\n      "reasoning": "How this was derived from the source text"\n    }\n  ],\n  "new_entities": [\n    {\n      "id": "type:slug",\n      "name": "Display Name",\n      "type": "person|organization|event|etc",\n      "description": "2-3 sentence description"\n    }\n  ]\n}`,
          max_tokens: 4096,
        }
      );
      const text = extractText(data);
      const parsed = tryParseJSON(text);
      if (parsed) { setExtraction(parsed); addLog(`Extracted ${parsed.assertions?.length || 0} assertions, ${parsed.new_entities?.length || 0} new entities`, "success"); }
      else { addLog("Could not parse extraction results", "warn"); setExtraction({ raw: text }); }
    } catch (e) { addLog("Error: " + e.message, "error"); }
    setLoadingKey("extract", false);
  }, [ingestText, entities, addLog]);

  const commitAssertion = useCallback(async (assertion) => {
    addLog(isSandbox ? `Haiku committing assertion via MCP...` : `Committing: ${assertion.claim.slice(0, 60)}...`);
    try {
      await cortexCreateAssertion({
        entity_id: assertion.entity_id,
        claim: assertion.claim,
        confidence: assertion.confidence,
        evidence: assertion.evidence,
        evidence_uris: [`workbench:ingest-${new Date().toISOString().slice(0, 10)}`],
      });
      setCommitted((prev) => [...prev, assertion.claim]);
      addLog(`Committed: ${assertion.claim.slice(0, 50)}...`, "success"); return true;
    } catch (e) { addLog("Commit error: " + e.message, "error"); return false; }
  }, [addLog, isSandbox]);

  const types = [...new Set(entities.map((e) => e.type))].sort();
  const filteredEntities = typeFilter ? entities.filter((e) => e.type === typeFilter) : entities;
  const groupedEntities = types.reduce((acc, type) => {
    const items = filteredEntities.filter((e) => e.type === type);
    if (items.length) acc[type] = items.sort((a, b) => a.name.localeCompare(b.name));
    return acc;
  }, {});

  return (
    <div className="h-screen flex flex-col" style={{ background: "#07070d", color: "#d4d4dc", fontFamily: "'IBM Plex Sans', 'SF Pro Text', system-ui, sans-serif" }}>
      <div className="flex items-center justify-between px-5 py-3 border-b" style={{ borderColor: "#1a1a2e", background: "#0a0a14" }}>
        <div className="flex items-center gap-3">
          <div className="w-2 h-2 rounded-full bg-amber-400 animate-pulse" />
          <h1 className="text-base font-semibold tracking-wide" style={{ color: "#c8a24e", fontFamily: "'IBM Plex Mono', 'SF Mono', monospace" }}>CORTEX WORKBENCH</h1>
          <span className="text-xs px-2 py-0.5 rounded" style={{ background: "#1a1a2e", color: "#666680" }}>v2.2.0</span>
          <span className="text-xs px-2 py-0.5 rounded" style={{ background: isSandbox ? "#1a2e1a" : "#1a1a2e", color: isSandbox ? "#4a9e6a" : "#888898" }}>{isSandbox ? "sandbox" : "standalone"}</span>
        </div>
        <div className="flex items-center gap-1">
          <TabButton active={tab === "entities"} onClick={() => setTab("entities")}>Entities</TabButton>
          <TabButton active={tab === "ingest"} onClick={() => setTab("ingest")}>Ingest</TabButton>
          <button onClick={() => setShowSettings(!showSettings)} className={`px-3 py-2 text-sm transition-all ${showSettings ? "text-amber-400" : "text-slate-500 hover:text-slate-300"}`}>⚙</button>
        </div>
      </div>
      {showSettings && (
        <div className="px-5 py-3 border-b space-y-2" style={{ borderColor: "#1a1a2e", background: "#0c0c16" }}>
          {isSandbox ? (
            <div className="space-y-2">
              <div className="flex items-center gap-3">
                <span className="text-xs" style={{ color: "#4a9e6a" }}>✓ Sandbox mode</span>
                <p className="text-xs" style={{ color: "#444460" }}>Auth via claude.ai session. Data + AI route through Anthropic API + MCP.</p>
              </div>
              <div className="flex items-center gap-4 text-xs" style={{ color: "#555570" }}>
                <span>Models:</span>
                <span style={{ color: "#6a9eca" }}>Haiku</span>
                <span style={{ color: "#444460" }}>→ MCP ops, extraction</span>
                <span style={{ color: "#c8a24e" }}>Sonnet</span>
                <span style={{ color: "#444460" }}>→ descriptions</span>
              </div>
            </div>
          ) : (
            <>
              <div className="flex items-center gap-3">
                <label className="text-xs font-medium w-24 flex-shrink-0" style={{ color: "#666680" }}>VORTEX TOKEN</label>
                <input
                  type="password"
                  value={token}
                  onChange={(e) => saveToken(e.target.value)}
                  placeholder="Paste your Vortex bearer token"
                  className="flex-1 px-3 py-1.5 rounded text-sm focus:outline-none focus:ring-1 focus:ring-amber-400/30"
                  style={{ background: "#0a0a14", border: "1px solid #1e1e32", color: "#c8c8d8", fontFamily: "'IBM Plex Mono', monospace" }}
                />
                {token && <span className="text-xs" style={{ color: "#4a9e6a" }}>✓ Set</span>}
              </div>
              <p className="text-xs" style={{ color: "#444460" }}>Vortex bearer token — authenticates data ops (cortex-api) and AI ops (LLM proxy). Persisted in localStorage.</p>
            </>
          )}
        </div>
      )}
      {error && (
        <div className="px-5 py-2 text-sm flex items-center justify-between" style={{ background: "#1a0a0a", color: "#e06060" }}>
          <span>{error}</span>
          <button onClick={() => { setError(null); loadEntities(); }} className="text-xs underline">Retry</button>
        </div>
      )}
      <div className="flex-1 flex overflow-hidden">
        {tab === "entities" && (
          <>
            <div className="w-72 flex-shrink-0 border-r overflow-y-auto" style={{ borderColor: "#1a1a2e", background: "#0a0a12" }}>
              <div className="p-3 border-b" style={{ borderColor: "#1a1a2e" }}>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-medium" style={{ color: "#666680" }}>{entities.length} ENTITIES</span>
                  {loading.entities && <Spinner />}
                </div>
                <div className="flex flex-wrap gap-1">
                  <button onClick={() => setTypeFilter(null)} className={`text-xs px-2 py-0.5 rounded transition-all ${!typeFilter ? "bg-amber-400/20 text-amber-400" : "text-slate-600 hover:text-slate-400"}`}>all</button>
                  {types.map((t) => {
                    const meta = TYPE_META[t] || {};
                    return (<button key={t} onClick={() => setTypeFilter(typeFilter === t ? null : t)} className={`text-xs px-2 py-0.5 rounded transition-all ${typeFilter === t ? "bg-amber-400/20 text-amber-400" : "text-slate-600 hover:text-slate-400"}`}>{meta.icon || "•"} {t.replace("_", " ")}</button>);
                  })}
                </div>
              </div>
              <div className="p-2 space-y-1">
                {Object.entries(groupedEntities).map(([type, items]) => (
                  <div key={type}>
                    <div className="px-2 pt-2 pb-1 text-xs font-medium tracking-wider" style={{ color: "#444460" }}>{(TYPE_META[type]?.icon || "•") + " " + type.replace("_", " ").toUpperCase()}</div>
                    {items.map((e) => (<EntityListItem key={e.id} entity={e} selected={selectedId === e.id} onClick={() => loadDetail(e.id)} />))}
                  </div>
                ))}
                {entities.length === 0 && !loading.entities && (
                  <div className="p-4 text-center text-sm" style={{ color: "#444460" }}>No entities loaded.<button onClick={loadEntities} className="block mt-2 text-amber-400/70 hover:text-amber-400 text-xs">Retry</button></div>
                )}
              </div>
            </div>
            <div className="flex-1 overflow-y-auto p-6">
              {loading.detail && (<div className="flex items-center gap-3 justify-center py-20"><Spinner size="w-5 h-5" /><span style={{ color: "#666680" }}>{isSandbox ? "Haiku retrieving entity via MCP..." : "Loading entity..."}</span></div>)}
              {!detail && !loading.detail && (<div className="flex items-center justify-center py-20" style={{ color: "#333350" }}><span>Select an entity to view details</span></div>)}
              {detail && !loading.detail && (
                <div className="max-w-3xl space-y-6">
                  <div>
                    <div className="flex items-center gap-3 mb-1">
                      <span className="text-2xl">{TYPE_META[detail.type]?.icon || "•"}</span>
                      <h2 className="text-xl font-semibold" style={{ color: "#e0e0ec" }}>{detail.name}</h2>
                    </div>
                    <p className="text-xs font-mono" style={{ color: "#555570" }}>{detail.id}</p>
                  </div>
                  <div className="rounded-lg border p-4" style={{ borderColor: "#1e1e32", background: "#0d0d18" }}>
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-xs font-medium tracking-wide" style={{ color: "#666680" }}>DESCRIPTION</span>
                      <button onClick={generateDescription} disabled={loading.description} className="text-xs px-3 py-1 rounded transition-all disabled:opacity-40" style={{ background: "#c8a24e20", color: "#c8a24e" }}>
                        {loading.description ? "Generating..." : detail.notes ? "Regenerate" : "Generate with Claude"}
                      </button>
                    </div>
                    {detail.notes && !proposedDesc && (<p className="text-sm leading-relaxed" style={{ color: "#b0b0c0" }}>{detail.notes}</p>)}
                    {!detail.notes && !proposedDesc && !loading.description && (<p className="text-sm italic" style={{ color: "#444460" }}>No description yet. Generate one to enable entity resolution.</p>)}
                    {loading.description && (<div className="flex items-center gap-2 py-2"><Spinner /><span className="text-sm" style={{ color: "#666680" }}>Sonnet writing description...</span></div>)}
                    {proposedDesc && (
                      <div className="space-y-3">
                        <div className="rounded-md border p-3" style={{ borderColor: "#c8a24e30", background: "#c8a24e08" }}>
                          <p className="text-sm leading-relaxed" style={{ color: "#d0d0dc" }}>{proposedDesc}</p>
                        </div>
                        <div className="flex gap-2">
                          <button onClick={() => commitDescription(proposedDesc)} disabled={loading.commitDesc} className="text-xs px-4 py-1.5 rounded font-medium transition-all" style={{ background: "#2a6a4a", color: "#80e0a0" }}>{loading.commitDesc ? "Committing..." : "✓ Commit"}</button>
                          <button onClick={() => setProposedDesc(null)} className="text-xs px-4 py-1.5 rounded transition-all" style={{ background: "#1a1a2e", color: "#888" }}>✗ Discard</button>
                          <button onClick={generateDescription} disabled={loading.description} className="text-xs px-4 py-1.5 rounded transition-all" style={{ background: "#1a1a2e", color: "#888" }}>↻ Regenerate</button>
                        </div>
                      </div>
                    )}
                  </div>
                  <div>
                    <div className="flex items-center gap-2 mb-3"><span className="text-xs font-medium tracking-wide" style={{ color: "#666680" }}>ASSERTIONS ({(detail.assertions || []).length})</span></div>
                    <div className="space-y-2">
                      {(detail.assertions || []).map((a, i) => (<AssertionCard key={a.id || i} assertion={a} />))}
                      {(!detail.assertions || detail.assertions.length === 0) && (<p className="text-sm italic py-2" style={{ color: "#444460" }}>No assertions on this entity.</p>)}
                    </div>
                  </div>
                </div>
              )}
            </div>
          </>
        )}
        {tab === "ingest" && (
          <div className="flex-1 overflow-y-auto p-6">
            <div className="max-w-4xl space-y-6">
              <div>
                <h2 className="text-lg font-semibold mb-1" style={{ color: "#e0e0ec" }}>Knowledge Ingestion</h2>
                <p className="text-sm" style={{ color: "#666680" }}>Paste journal entries, notes, or any text. Claude will extract entities and assertions for your review.</p>
              </div>
              <div>
                <textarea value={ingestText} onChange={(e) => setIngestText(e.target.value)} placeholder="Paste text here..." rows={8} className="w-full rounded-lg border p-4 text-sm resize-y focus:outline-none focus:ring-1 placeholder-slate-700" style={{ borderColor: "#1e1e32", background: "#0a0a14", color: "#c8c8d8", fontFamily: "'IBM Plex Mono', monospace" }} />
                <div className="flex items-center justify-between mt-2">
                  <span className="text-xs" style={{ color: "#444460" }}>{ingestText.length > 0 ? `${ingestText.split(/\s+/).filter(Boolean).length} words` : ""}</span>
                  <button onClick={extractKnowledge} disabled={loading.extract || !ingestText.trim()} className="px-5 py-2 rounded-lg text-sm font-medium transition-all disabled:opacity-30" style={{ background: "#c8a24e", color: "#0a0a14" }}>{loading.extract ? "Extracting..." : "Extract Knowledge"}</button>
                </div>
              </div>
              {loading.extract && (<div className="flex items-center gap-3 py-8 justify-center"><Spinner size="w-5 h-5" /><span style={{ color: "#666680" }}>{isSandbox ? "Haiku extracting knowledge..." : "Haiku extracting knowledge..."}</span></div>)}
              {extraction && !loading.extract && (
                <div className="space-y-6">
                  {extraction.new_entities?.length > 0 && (
                    <div>
                      <h3 className="text-sm font-medium mb-3" style={{ color: "#c8a24e" }}>New Entities ({extraction.new_entities.length})</h3>
                      <div className="space-y-2">
                        {extraction.new_entities.map((ent, i) => (
                          <div key={i} className="rounded-lg border p-3" style={{ borderColor: "#c8a24e30", background: "#c8a24e08" }}>
                            <div className="flex items-center gap-2 mb-1"><span>{TYPE_META[ent.type]?.icon || "•"}</span><span className="text-sm font-medium" style={{ color: "#d0d0dc" }}>{ent.name}</span><span className="text-xs font-mono" style={{ color: "#555570" }}>{ent.id}</span></div>
                            {ent.description && (<p className="text-xs mt-1" style={{ color: "#888898" }}>{ent.description}</p>)}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {extraction.assertions?.length > 0 && (
                    <div>
                      <h3 className="text-sm font-medium mb-3" style={{ color: "#c8a24e" }}>Extracted Assertions ({extraction.assertions.length})</h3>
                      <div className="space-y-2">
                        {extraction.assertions.map((a, i) => {
                          const isCommitted = committed.includes(a.claim);
                          const confClass = CONF_COLORS[a.confidence] || CONF_COLORS.believed;
                          return (
                            <div key={i} className={`rounded-lg border p-3 transition-all ${isCommitted ? "opacity-50" : ""}`} style={{ borderColor: "#1e1e32", background: "#0d0d18" }}>
                              <div className="flex items-start justify-between gap-3">
                                <div className="flex-1 space-y-1">
                                  <div className="flex items-center gap-2 flex-wrap">
                                    <span className="text-xs font-mono px-1.5 py-0.5 rounded" style={{ background: "#1a1a2e", color: "#888898" }}>{a.entity_id}</span>
                                    <Badge className={confClass}>{a.confidence}</Badge>
                                  </div>
                                  <p className="text-sm" style={{ color: "#d0d0dc" }}>{a.claim}</p>
                                  <p className="text-xs" style={{ color: "#555570" }}>{a.evidence}</p>
                                  {a.reasoning && (<p className="text-xs italic" style={{ color: "#444460" }}>Reasoning: {a.reasoning}</p>)}
                                </div>
                                <div className="flex-shrink-0">
                                  {isCommitted ? (<span className="text-xs" style={{ color: "#4a9e6a" }}>✓ Done</span>) : (
                                    <button onClick={() => commitAssertion(a)} className="text-xs px-3 py-1.5 rounded font-medium transition-all hover:brightness-110" style={{ background: "#2a6a4a", color: "#80e0a0" }}>Commit</button>
                                  )}
                                </div>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                      {extraction.assertions.length > 0 && (
                        <div className="mt-4 flex gap-2">
                          <button onClick={async () => { for (const a of extraction.assertions) { if (!committed.includes(a.claim)) { await commitAssertion(a); } } }} className="text-xs px-4 py-2 rounded font-medium transition-all" style={{ background: "#2a6a4a", color: "#80e0a0" }}>Commit All ({extraction.assertions.length - committed.length} remaining)</button>
                          <button onClick={() => { setExtraction(null); setCommitted([]); }} className="text-xs px-4 py-2 rounded transition-all" style={{ background: "#1a1a2e", color: "#888" }}>Clear Results</button>
                        </div>
                      )}
                    </div>
                  )}
                  {extraction.raw && (
                    <div className="rounded-lg border p-4" style={{ borderColor: "#1e1e32", background: "#0d0d18" }}>
                      <p className="text-xs font-medium mb-2" style={{ color: "#666680" }}>Raw extraction (could not parse as JSON):</p>
                      <pre className="text-xs whitespace-pre-wrap" style={{ color: "#888898", fontFamily: "'IBM Plex Mono', monospace" }}>{extraction.raw}</pre>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
      <div className="border-t" style={{ borderColor: "#1a1a2e", background: "#08080f" }}>
        <button onClick={() => { const el = logRef.current; if (el) el.style.display = el.style.display === "none" ? "block" : "none"; }} className="w-full px-5 py-1.5 flex items-center justify-between text-xs" style={{ color: "#555570" }}>
          <span style={{ fontFamily: "'IBM Plex Mono', monospace" }}>ACTIVITY — {log.filter((l) => l.type === "error").length > 0 && "⚠ "}{log.length > 0 ? log[log.length - 1].msg.slice(0, 80) : "Ready"}</span>
          <span>▾</span>
        </button>
        <div ref={logRef} style={{ display: "none" }} className="max-h-32 overflow-y-auto px-5 pb-2">
          {log.map((l, i) => (<div key={i} className="text-xs py-0.5 flex gap-2" style={{ fontFamily: "'IBM Plex Mono', monospace" }}><span style={{ color: "#333350" }}>{l.ts}</span><span style={{ color: l.type === "error" ? "#e06060" : l.type === "success" ? "#4a9e6a" : l.type === "warn" ? "#c8a24e" : "#555570" }}>{l.msg}</span></div>))}
        </div>
      </div>
    </div>
  );
}

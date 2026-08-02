---
trigger_match_terms: ["mcp-tool-loop-trace-matrix", "mcp_tool_loop_trace_matrix", "xai", "grok", "mcp", "reliability", "probe", "ladder", "pipelines-rag-mcp", "investigation", "cross-provider", "regression"]
description: On xAI/Grok MCP reliability investigation, cross-provider MCP regression after adapter/routing changes, or validating frontier tool-loop fixes — run the probe ladder L0–L3.
---

# MCP Tool-Loop Trace Matrix

**Version:** 1.1  
**Updated:** 2026-07-10  
**Authority level:** MEDIUM — cross-provider MCP reliability diagnostics.

**Companion to:** `ulg-architecture.md` (service lifecycle), `agent_skill:dispatch-workflow`.
**Evidence run:** `cortex:notes/system/threads/mcp-reliability-trace-matrix-2026-06-03.md` (thread 1207).
**Cost ruling:** agent-bus 4745 Rec 4 / Wave 0.3 — probes must not enter the reviewer hydration path.

## When to read this skill

Read before:

- Investigating **xAI/Grok MCP** failures or regressions
- Comparing **cross-provider MCP reliability** (OpenAI, Anthropic, Google, xAI)
- Validating adapter/routing fixes post-deploy (especially Gemini replay/envelope work)
- Deciding whether "Grok can't MCP" is infra, surface mismatch, or task hardness

## Core rule

`probe_ladder(L0→L3) ∧ tag_surface ∧ record_execution_id ∧ run_golden_control(openai/gpt-5.5)`  
`pass(L0) ∧ fail(L2) ⇒ likely write/schema friction — not admission`  
`golden_control_fails ⇒ fix infra first — ¬ blame provider`  
`probe ⇒ prefer(chat_-mcp) ∧ ¬role(reviewer) ∧ max_tool_turns≤3_for_L0`

## Probe routing (cost — binding)

Probes measure tool-loop FIRE+CONSUME. They are **not** reviews. Fat `team_dispatch` hydration (role briefing + compaction) turned L0/L3 "probes" into 55–88k prompt burns (4745 consult; 2026-07-10 L3 residual).

| Path | When | Cost shape |
|---|---|---|
| **Preferred — chat `-mcp` proxy** | Default L0–L3 reliability ladder | Minimal prompt; hundreds of tokens for golden L0 |
| **Alternate — frontier/team_dispatch** | Explicitly validating production `client_side_injection` surface (tag it) | Hydration tax — use artisan/skeptic, never reviewer |
| **Forbidden** | `role=reviewer` + `caller_agent` matching `mcp-l*-probe` / `mcp-trace-matrix` | Admission reject (optional hard guard) |

Rules:

1. Default probe surface = `/v1/chat/completions` with model ending `-mcp` (e.g. `openai/gpt-5.5-mcp`, `xai/grok-4.5-mcp`), only the tool under test exposed, `max_tool_turns≤3` for L0 (raise only as ladder needs).
2. If you must use `team_dispatch(op=generate, mcp=true)` to tag the production native Responses loop: use `role=artisan` or `role=skeptic` (or bare frontier dispatch) — **never** `role=reviewer`.
3. Set `caller_agent` to `mcp-l0-probe` / `mcp-l1-probe` / `mcp-l3-probe` / `mcp-trace-matrix` so traces and the admission guard can identify probes.
4. Still tag the surface column — chat `-mcp` ≠ frontier client loop; do not compare across surfaces without noting it.

## Preflight (mandatory)

1. Deploy code under test:
   ```
   manage(action="sync_restart", service="stargate")
   manage(action="wait_healthy", service="stargate", timeout=120)
   manage(action="sync_restart", service="cloud_proxy")
   manage(action="wait_healthy", service="cloud_proxy", timeout=120)
   ```
2. Confirm model in cloud catalog (UDS):
   ```bash
   curl -sf --unix-socket /tmp/universal-protocol/cloud-proxy.sock \
     http://localhost/catalog | jq -r '.[].id' | rg 'xai/grok|openai/gpt-5.5|google/gemini'
   ```
3. Optional Gemini wire trace: restart stargate with `GEMINI_TOOL_LOOP_TRACE=1` in its environment.

## Surfaces to tag (record on every probe)

| Surface | How invoked | Loop owner |
|---|---|---|
| **frontier client loop** | `POST /api/v1/frontier/dispatch` `mcp=true` | `native_loop` + provider adapter |
| **Anthropic remote_mcp** | same + `remote_mcp=true` (default for anthropic) | Provider server-side MCP |
| **chat `-mcp` proxy loop** | `/v1/chat/completions` model ending `-mcp` | `McpToolExecutor.run_tool_loop` |
| **xAI Responses bridge** | grok-4 + `-mcp` or multi-agent | Provider `type:mcp` tool |

Never compare probes across surfaces without noting the surface column.

## Probe ladder

Run **golden control** (`openai/gpt-5.5-mcp` on chat proxy, or `openai/gpt-5.5` on frontier when tagging that surface) at the same level before interpreting provider failure.

### L0 — single read-only tool (preferred: chat `-mcp`)

```bash
# Preferred cost path — chat -mcp proxy loop (tag surface: chat_-mcp)
curl -sS http://localhost:9999/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "xai/grok-4.5-mcp",
    "max_tokens": 256,
    "messages": [{
      "role": "user",
      "content": "Call cortex tool stats with arguments={}. Reply in one sentence with the entity count or explicit error."
    }]
  }'
```

Alternate (production-surface validation only — tag `frontier client loop`):

```json
POST http://localhost:9999/api/v1/frontier/dispatch
{
  "op": "generate",
  "model": "xai/grok-4.5",
  "mcp": true,
  "max_tool_turns": 3,
  "caller_agent": "mcp-l0-probe",
  "messages": [{
    "role": "user",
    "content": "Call cortex tool stats with arguments={}. Reply in one sentence with the entity count or explicit error."
  }]
}
```

Poll frontier: `GET /api/v1/pipelines/executions/{execution_id}?wait=55`  
If using `team_dispatch` instead of raw frontier: `role=artisan|skeptic`, never `reviewer`.

### L1 — two read-only tools (replay fidelity)

Same dispatch; user prompt:

```text
Step 1: cortex tool stats with arguments={}.
Step 2: cortex tool entity_get with arguments={"entity_id":"todo:gemini-mcp-tool-loop-fidelity-fixes","intent":"card"}.
Summarize both in two sentences.
```

`max_tool_turns`: 5

### L2 — write / staged mutation (Grok stress)

Only when operator approves cortex writes. Use staged assert or dry_run if available:

```text
Call cortex tool observe with arguments={"claim":"mcp trace probe","agent":"mcp-trace-matrix"}.
Report ok/error from tool result.
```

### L3 — adversarial (historical failure class)

- ≥5 tool turns, mixed read/write
- Parallel function calls if model supports
- Streaming (`generation_options.stream=true`) for Gemini
- Thinking on/off for Gemini 3 (`reasoning_effort`)

## Golden control

Always run **same probe level** with:

```json
{"model": "openai/gpt-5.5", "mcp": true, "remote_mcp": false}
```

GPT-5.5 is the **production golden path** — if it fails, stop and fix infra. Do not tune Grok/Gemini until golden passes.

**A/B note:** When experimenting with improved tool-result envelopes on GPT, keep this control path unchanged; gate treatments behind explicit flags (see thread 1207 sidecar P1).

## Record (per probe)

| Field | Source |
|---|---|
| `probe_id` | your label |
| `model` | workspace catalog id |
| `remote_mcp` | true/false/null |
| `surface` | from table above |
| `execution_id` | 202 admit body |
| `status` | terminal poll |
| `duration_s` | terminal.result.duration_s |
| `content_preview` | first 200 chars |
| `finish_reason` | events if available |

Forensics:

```bash
scripts/query-events --op pipeline-trace \
  --param execution-id=<UUID> --limit 120 --compact
```

Write durable summary to cortex sidecar:

```
fs(sandbox="cortex", op="write",
   path="notes/system/threads/mcp-trace-<topic>-YYYY-MM-DD.md", content="...")
```

Harness mirror (ephemeral): `tmp/mcp-trace-matrix/run_traces.py`

## Interpretation cheatsheet

| Pattern | Likely cause | Next step |
|---|---|---|
| L0 pass, L2 fail | write/schema/idempotency | diff tool result shape; check friction tracker |
| Golden pass, Grok fail L1+ | xAI adapter / Responses loop | compare `append_tool_round` wire; check effort suffix model |
| Anthropic remote pass L0, fail L3 | timeout / empty completion | check `remote_mcp` events; thread 968 pattern |
| Gemini pass L0, fail L1 streaming | replay/thoughtSignature | `GEMINI_TOOL_LOOP_TRACE=1`; inspect turn-2 contents |
| All fail | stargate/cloud_proxy/catalog | health + catalog preflight |

## xAI/Grok-specific notes

- **Easy probes often pass** (2026-06-03: `xai/grok-4.3` L0 ~6.7s) — do not close "Grok MCP fixed" on L0 alone.
- **Effort suffix models** (`xai/grok-4.3__effort_medium`) are distinct catalog IDs — probe the exact ID production uses.
- **grok.com web** MCP uses `call_connected_tool` shape — separate from the `team_dispatch(op=generate, mcp=True)` client loop.
- **Multi-agent xAI** may suppress client-side MCP tools — check admission `tool.suppressed` events.

## Anti-patterns

- Declaring provider broken after one failed complex task without ladder + golden control
- Comparing Grok web connected-tool failures to `team_dispatch(op=generate, mcp=True)` results without tagging surface
- Changing GPT-5.5 default tool-result shape without A/B flag
- Storing only `tmp/` results — sidecar + execution IDs required for revisit
- Running probes via `role=reviewer` / fat team_dispatch hydration when chat `-mcp` would answer the reliability question
- Omitting `caller_agent=mcp-l*-probe` so cost forensics cannot separate probes from real reviews

## Minimal operating summary

1. Preflight services + catalog
2. Prefer chat `-mcp` L0 → L1 → (L2 if approved); golden control each step
3. Tag surface + record execution_id (or chat request_id)
4. Sidecar findings; agent-bus handoff if cross-seat exploration needed

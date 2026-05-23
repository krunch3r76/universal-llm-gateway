# Phase D Pre-Cutover Probe Harnesses

Gate probes for `plan:dual-seat-mcp-reliability` Phase D (Pre-D0-2 and Pre-D0-3).

## File inventory

| File | Probe | Gate |
|---|---|---|
| `p7_dispatcher_attention.py` | P7 — Claude dispatcher attention/interference | Pre-D0-2 |
| `p8_prompt_cache_invariance.py` | P8 — Prompt-cache invariance | Pre-D0-3 |

P10 (≤24 tools cap) is covered by `test_route_claude.py::test_claude_primary_tools_count`. No additional harness needed.

---

## P7 — Claude Dispatcher Attention/Interference

**Gate**: Pre-D0-2. Must PASS before D0 cutover.

**Design method**: Hybrid (iii) — descriptor-level invariants + keyword-match proxy battery.

### Why hybrid instead of real LLM calls

Real LLM calls (option i) require:
- A live Claude API session routed through the live `/mcp`
- Operator time and API cost (~$0.10–$1.00 for 15 prompts × 4 message turns)
- The container to already serve the Phase D manifest (post-D0)

For the **pre-cutover** gate, the goal is to verify that the new 11-tool manifest won't confuse Claude's dispatcher selection. The descriptor-level proxy gives deterministic, zero-cost coverage of the structural risk (ambiguous descriptions, overlapping keywords). The operator can run real-LLM validation after D0 as part of the 7-day monitoring window.

### Part A: Descriptor invariants (always runnable)

- No duplicate `tool_name` across domains
- No empty descriptions
- Non-generic op names not shared across domains
- Near-neighbor Jaccard similarity matrix — flag pairs ≥ 0.25 (WARN) or ≥ 0.40 (NEEDS-HUMAN-CALL)

### Part B: Keyword-match prompt battery

19 prompts across all 11 domains (including intentional near-neighbor edge cases). Each prompt is matched against domain keyword sets (domain name + op names + tokenized description). Best-match domain must equal expected domain.

Gate: PASS ≥ 80% accuracy; WARN 67–79%; FAIL < 67%.

### Running

```bash
# Human-readable:
python3 services/mcp-server/probes/p7_dispatcher_attention.py

# JSON output (for cortex assertion):
python3 services/mcp-server/probes/p7_dispatcher_attention.py --json
```

### Real-LLM operator checklist (post-D0 addition)

After D0 cutover, the operator should run 5 representative prompts in a Claude session to verify:

1. "reply to agent-bus thread 480" → uses `agent_bus(op=reply, ...)`
2. "save this observation to cortex" → uses `cortex(op=assert, ...)`
3. "rebuild the mcp service" → uses `manage(op=rebuild, ...)`
4. "search for RAG articles about routing" → uses `rag(op=search, ...)`
5. "dispatch to claude-opus for this prompt" → uses `dispatch(op=frontier, ...)`

Record results in a cortex assertion on `plan_phase:dual-seat-mcp-reliability/phase-d`.

---

## P8 — Prompt-Cache Invariance

**Gate**: Pre-D0-3. Must PASS before D0 cutover.

**Design method**: Multi-level proxy (Anthropic cache tokens not in event payloads).

### Why not real Anthropic cache tokens

`cache_read_input_tokens` / `cache_creation_input_tokens` come from Anthropic API response headers and are not currently propagated to the gateway event service. This is a known instrumentation gap (not a blocker). The `response_bytes` proxy is sufficient for Phase D gating:

- If `derive_claude_manifest()` produces the same descriptor payload on every call → same sha256 → Anthropic cache key unchanged → cache hit rate stable.
- The `mcp.request.completed` events for `mcp_method=tools/list` record `response_bytes` = the raw MCP descriptor payload size. Stable `response_bytes` across reboots confirms the descriptor didn't change.

### Part A: Manifest byte-stability

Derives the Claude manifest 5 times and verifies all 5 sha256 values are identical. PASS criterion is determinism.

### Part B: Event service baseline

Captures the pre-D0 `tools/list` response_bytes from `mcp.request.completed` events filtered to `seat_class=claude`. Pre-D0 baseline = 39946 bytes (6 dispatcher tools). After D0 rebuild (11 tools), the new value will be larger — if stable across 2 restarts, the Anthropic cache will warm to the new size and hold.

### D9 bug diagnosis

The `d9-cache-invariance-probe.sh` returned empty events because:

1. It used a custom socket protocol with `{"op": "query", "params": {"signal": "..."}}` — the event service socket accepts named operations (`recent-failures`, `signal-events`, etc.) or raw SQL (via `--sql`). The unrecognized format returned empty silently.

2. Even with a correct query, `mcp.request.completed` payloads don't contain `cache_read_input_tokens`. Anthropic API cache metrics are not instrumented in the gateway event service.

Fix: use `scripts/query-events --sql` with JSON path extraction. The corrected SQL is embedded in `p8_prompt_cache_invariance.py` and works correctly.

### Running

```bash
# Human-readable:
python3 services/mcp-server/probes/p8_prompt_cache_invariance.py

# JSON output with saved baseline:
python3 services/mcp-server/probes/p8_prompt_cache_invariance.py --json --baseline-file tmp/scratch/p8-baseline-$(date +%Y%m%d).json
```

### Post-D0 monitoring gate

After D0 cutover and container rebuild:

```bash
# Verify new tools/list response_bytes is stable (run twice, 10 min apart):
scripts/query-events --sql "SELECT json_extract(payload, '$.response_bytes') FROM events WHERE signal='mcp.request.completed' AND json_extract(payload, '$.mcp_method')='tools/list' AND json_extract(payload, '$.seat_class')='claude' ORDER BY created_at DESC LIMIT 5"
```

PASS criterion: all 5 most-recent `response_bytes` values are identical (byte-stable descriptor → cache stable).

---

## Gate summary (pre-D0 cutover)

| Probe | Status | Verdict | Evidence |
|---|---|---|---|
| **P7** | Run at time of probe build | See probe output | Part A: violations count; Part B: accuracy % |
| **P8** | Run at time of probe build | See probe output | Manifest sha256 stability + baseline bytes |
| **P10** | Covered by test_route_claude.py | PASS (16/16 tests) | test_claude_primary_tools_count |

After P7 + P8 PASS → proceed to D0 cutover sequence per spec §8.2.

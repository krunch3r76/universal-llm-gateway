---
name: dispatch-workflow
description: Read before calling team_dispatch for peer consultation, batch dispatch, or sub-agent workflow — surface/seat/role/model, verification, tool-loop budgets, batch polling.
trigger_match_terms: ["dispatch-workflow", "dispatch_workflow", "team_dispatch", "subagent", "dispatch-delegation", "calling", "peer", "consultation", "batch", "dispatch", "sub-agent"]
---

# Dispatch Workflow

**Authority:** master. Sub-workflows extend, never override.  
**Trigger:** any `team_dispatch` call (code surface only — see Surface gate). Read once per session before first dispatch.

**Mapped packs:** `rag(op=list_mapped)` → `rag(op=search, mapped=true, scope=…, query=…)`; ¬ URI/fs-read; live search stays `mapped=false`/omit.

## Surface gate (life vs code)

Life MCP excludes CODE_EXTRA (`team_dispatch`, `panel_dispatch`, `pipeline`,
`manage`, `observability`). Cognitive workflow rules in this skill run on every seat.
CODE_EXTRA call sites = **code MCP only**. On life/claude.ai: (1) run cognitive
legs in-seat; (2) `agent_bus` ask a code seat to fire the transport; or (3)
stamp honest deferral / operator bridge — ¬ call CODE_EXTRA from life.

## 0. Pick surface by capability (code surface only)

Read `consult-routing` before consult/review routing. **All recipes in this section = code MCP only.**

| Goal | Surface |
|---|---|
| Model answer/reasoning | `team_dispatch(op="generate", role=<api_role>, contract=…, dispatch_thread_id=…, model?="provider/model")` |
| ≥2-provider material-decision panel | `panel_dispatch`; see `consensus-steelman-posture` |
| Code changes (settled, unattended) | `team_dispatch(op="generate", seat="cursor-sdk", contract="implement", source_ref="todo:{slug}", dispatch_thread_id=…)` — default; attended `cursor-implement`/`web-implement` handoff is fallback |
| Packet heavy reasoning / web research | `team_dispatch(op="handoff", role="web-consult", …)` or `panel_dispatch` |
| Manual web handoff / self-handoff | `team_dispatch(op="handoff", role="web-consult", packet_path=…)` |
| Cursor IDE consult / implement | `handoff` roles `cursor-consult` / `cursor-implement` |
| Work this session can do with MCP | Do locally; do not generate to own seat |

Model family ≠ surface. `want_grok_answer ⇒ team_dispatch(generate, role=<api_role>, model="xai/...")`; pick surface by capability, not model. Code execution routes through `cursor-sdk` (see consult-routing).

FOL:

```text
want_answer ⇒ team_dispatch(generate, api_role, contract∈{light-bounded,pure-mechanical}, dispatch_thread_id, model?)
want_code_changes ∧ settled ⇒ team_dispatch(generate, seat=cursor-sdk, contract∈{implement,light-bounded,pure-mechanical,wrap}, source_ref|packet_path, dispatch_thread_id)
want_code_changes ∧ ¬settled ⇒ R1_reasoning_first  # see consult-routing
want_answer ∧ grok_model ⇒ team_dispatch(generate, api_role, model="xai/…")  # role=surface, model=affordance
caller_has_local_mcp ∧ task∈local_surface ⇒ do_locally
manual_seat ∧ fresh_packet_context ⇒ team_dispatch(handoff, own_alias)
```

## 0a. Seat vs role vs model

| Axis | Meaning | Examples |
|---|---|---|
| `op` | Output channel | `generate`, `to_thread`, `handoff` |
| `role` | Functional API role, or handoff roster role | `reviewer`, `skeptic`, `web-consult`, `cursor-implement` |
| `model` | Optional API wire ID | `openai/gpt-5.5`, not `web-anthropic` |

| op | Action | Valid roles |
|---|---|---|
| `generate` | Cloud API call OR SDK executor; auto result thread; Stargate posts on behalf; poll via `agent_bus(wait)` from `poll_hint` | API roles (api_dispatchable): `reviewer`, `synthesizer`, `artisan`, `skeptic`. Plus `cursor-sdk` — SDK substrate (→ git_integration_worker), NOT an api_dispatchable role |
| `to_thread` | Same, delivered to named bus thread | Same API roles |
| `handoff` | Creates bus thread + pointer; no model; operator/IDE push | Handoff roster roles (`web-consult`, `cursor-consult`, `cursor-implement`); not raw seat slugs |

Delivery ownership: generate/to_thread results are posted by Stargate on behalf. The model must not be told “reply on this thread”; with `mcp=true` that can cause duplicate self-posts. Omit `mcp` for self-contained consults; reserve it for real investigation/tool use — and note that an **axis-2 skeptic densify/ratification citing live files IS such tool use**: set explicit `mcp=true` + `max_tool_turns≥15` (see `cheap-recon-before-escalation` § Skeptic dispatch mechanics), not silent omit or cargo-cult `mcp=false`.

Web seats are not generate targets. `role="web-anthropic"|"web"|"lead"|"investigator"` on generate/to_thread fails; `model=` does not turn an API call into a web session. If you are a web seat with MCP, do local MCP work locally; consult another model via API role.

Self-handoff is supported via matching handoff roster role to open a new bus thread to the same manual seat. Peer handoff: execute the packet on returned thread; dispatcher polls `result_handle` / `poll_hint`.

Handoff submit/wait (**code surface only** — see Surface gate):

```text
team_dispatch(handoff, role=web-consult|cursor-consult|cursor-implement, packet_path, subject)
read result_handle + poll_hint; surface push_reminder
agent_bus(wait, poll_hint.arguments_json) until complete=true
```

**Web-anthropic / life gate (before admit or before push):** Use the `life-handoff-corpus` skill (binding corpus + `ephemeral/handoffs/` mirror). Authoring depth: `handoff-packet-authoring` § Web-receiver priming — (1) **skill-inline** full bodies + early `allow_long_body` turn; slug-only `Use the <slug>` is not sufficient; (2) **prefer cortex-mirror** packet + corpus under `cortex://ephemeral/handoffs/` for fewer tool calls / faster response — `workspaces://` is readable; name it when exploration is encouraged. Coding implement stays cursor-sdk; do not send workspaces-only coding packets that need durability on life without a cortex mirror.

Never `pipeline(result)` for handoff; no `execution_id`. Poll returned `thread_id`, not “most recent thread”. Use `wait`, not fetch loops.

Handoff statuses: `awaiting_first_reply`, `complete`, telemetry/future states `awaiting_push`, `awaiting_reply`, `multi_turn`.

## Dispatch Workflow/0a. Seat vs role vs model
> `generate_roles()` (`libs/agent_seat/dispatch_role_catalog.py`) returns exactly `[reviewer, synthesizer, artisan, skeptic]`. `cursor-sdk` admits on `op=generate` via a distinct SDK-substrate path (`is_cursor_sdk_generate_admission` → `resolve_cursor_sdk_generate_target`, `config/routing/route_policy.yaml`), so it is intentionally absent from that list. Verification greps comparing `generate_roles()` output to prose must target the api-role row only.

## 1. Model string

`model MUST be "provider/model"`. Bare model returns provider 404; seat slug is not model ID.

✅ `openai/gpt-5.5`, `openai/gpt-5.4`, `anthropic/claude-opus-4-8`, `xai/grok-4`  
❌ `gpt-5.5`, `gpt-5`, `web-anthropic`, `openrouter/openai/gpt-5.5`

If user says “consult gpt-5.5,” resolve to `openai/gpt-5.5` without asking.

## 2. Model selection

```text
one_shot_inline_reasoning ∧ no_tools ⇒ openai/gpt-5.4 acceptable
mcp_tool_loop_with_file_io ⇒ openai/gpt-5.5
sensitive_domain ∨ content_preservation_critical ∨ high_fidelity_review ⇒ openai/gpt-5.5
peer_design_review ⇒ openai/gpt-5.5
bulk_3M_context_single_pass ⇒ SuperHeavy
seat=cursor-sdk ∧ contract=implement ⇒ cursor/composer-2.5 (role default; no model= required)
seat=cursor-sdk ∧ (recon|investigate-emphasis) ⇒ model=cursor/grok-4.5 (contract=light-bounded typical)
seat=cursor-sdk ∧ pure_mechanical_inventory ⇒ Composer OK
```

Origin: GPT-5.4 failed 28/29 tool-loop skill rewrites by budget exhaustion; GPT-5.5 succeeded 28/28 retry. Use 5.4 only for certain no-tools single-shot calls. cursor-sdk model split: implement stays Composer default; recon+investigate judgment overrides to `cursor/grok-4.5` (not API `xai/grok-4.5`).

## 3. Prompt shape

`|system_prompt| < 1KB ⇒ unreliable_at_scale`. Prefer 2–4KB with canonical rules, architecture, anti-patterns, and return shape. Prompt-token savings are false economy when failure rate rises.

## 4. Verification

`dispatch_metadata = self_report ≠ ground_truth`.

For file-writing dispatches, verify durable artifact via `fs list/read` in target sandbox. Inline consult content is itself the artifact; read it.

## 5. Tool-loop budget

```text
mcp=True ⇒ max_tool_turns ≥ 15 ∧ sandbox_explicit_prompt
mcp=True ∧ max_tool_turns < 10 ⇒ typical_flow_fails
```

A typical read→reason→write→return flow needs slack beyond 4 calls. System prompt must state `sandbox = 'cortex'` or `sandbox = 'workspaces'` verbatim; high-stakes paths should include absolute path too.

## 6. Batch dispatch

`N > 5 ⇒ poll_strategy=staging_dir_list`, not per-execution polling.

Fire all, wait coarse interval, list staging dir, diff against expected targets, inspect only missing execution_ids. For large source corpora: `source_file_count > 5 ∨ bytes > 100KB ⇒ mcp=True self-fetch`; return only small metadata, write full output to disk.

## 6b. Post-dispatch cleanup gate

∀ dedup/prune/trash/move/rename/overwrite on paths a prior dispatch may still write: **confirm upstream executor TERMINAL** before mutating (`consult-routing` § Post-dispatch output mutation gate). Partial progress (downloads done, indexing pending, closeout posted, `CURSOR_SDK_TIMEOUT` on bus) is not sufficient — a live bridge may recreate deleted files (friction 23842).

## 6a. Oversized tool-result context bombs

`dispatched_loop ∧ first_tool_result_multi_MB ⇒ context_overflow_before_reasoning`.

Unscoped audit/list/search can return MBs and fail as either `context_length_exceeded` or timeout with `model_call_count:0`. More timeout/tool turns do not fix bad input.

Before delegating any list/dump/audit/search, prove bounded payload:

- Scope `cortex(audit)` by `subject`; never global `kinds`-only audit in a dispatched loop.
- Use `assertions(filter, limit<=8)`, `entity_get(intent="card")`; avoid `full` on hubs.
- If oversized pointer (`rs_xxxx`) appears, re-query smaller; do not `retrieve()` inside loop.
- Probe once in lead context when uncertain.

## 7. Anti-patterns

CODE_EXTRA call names below are **code-surface vocabulary** (see Surface gate).

- Unprobed unscoped audit/list/search in a dispatched tool loop.
- GPT-5.4 + `mcp=True` + low tool budget.
- Per-execution polling for large batches.
- Trusting `status:"written"` without fs verification.
- Compact prompts for dense rule work.
- Reading huge corpora into caller context instead of dispatch self-fetch.
- Omitting sandbox in prompt.
- Retrying unchanged failed dispatches.
- Asking SuperHeavy to call local MCP tools.
- Bare model name.
- `team_dispatch(generate, role="web-anthropic"|"web"|"lead")` or trying `model=` to fix it.
- Handoff as web-seat offload for local MCP work.
- `pipeline(result)` after handoff.
- Polling most recent thread instead of returned handle.
- Client-side/fetch poll loops instead of one `agent_bus(wait)` call per check.
- Corpus dedup/prune/trash while a cursor-sdk ingest dispatch is still live.

## 8. Sub-workflows

| When | Read |
|---|---|
| Workflow lane case studies | `cortex://notes/system/specs/dispatch-workflow-case-studies.md` |
| Unattended code execution (cursor-sdk) | `consult-routing` § General execution lane + `cursor-sdk-instruction-standard` |

## Minimal operating summary

Surface first. Consult = generate API role + contract + dispatch thread. Build = build executor. Handoff = handoff roster role. Seat slugs are not model IDs. Always `provider/model`. GPT-5.5 for tool loops and fidelity work. `mcp=True` needs ≥15 tool turns, verbose prompt, sandbox-explicit instruction. Verify durable outputs by filesystem. Large batch polling via staging-dir diff. Change parameters before retrying.

# Web boot lead — session open (claude-web)

**Readers:** web-claude operators and packet authors wiring web-lead session prompts.

Companion: `skill-suggest-utilization.md` (delta discovery), `web-agent-orientation.md` (MVW index),
`handoff-packet-authoring.md` (dispatch packets).

---

## cortex_boot — call shape and defaults

**All MCP params are optional.** Bare `cortex_boot()` resolves to **`claude-cursor`**, not web.

| Goal | Call |
|---|---|
| Web lead (recommended) | `cortex_boot(agent="claude-web", role="lead")` |
| Web seat, no role anchor | `cortex_boot(agent="web")` → normalizes to `claude-web` |
| Equivalent axes | `cortex_boot(family="claude", platform="web", role="lead")` |

| Param | Default when omitted | Notes |
|---|---|---|
| `agent` | — | Primary seat slug; aliases: `web` → `claude-web`, `cursor` → `claude-cursor` |
| `family` / `platform` | `claude` / `cursor` | Used when `agent` absent or unparsable |
| `role` | none | `lead` / `reviewer` / … — annotates session; **does not** change seat slug |
| `transcript_id` | — | Continuation from a **closed** session transcript entity only |
| `principal` | — | e.g. `person:…` — principal context block at card head |
| `profile` | — | `"dispatch"` for dispatch-scoped inject + packet invariant parse |
| `packet_text` | — | With `profile="dispatch"`: parse `<invariants>` skill ids |

**Hold `session_id`** from the response for asserts, edges, and `session_close`.

**Cloud proxy:** system prompts may embed `cortex_boot(…)`; the proxy pre-executes and substitutes
`briefing_card` only. Invariant skill bodies are **operator-attached in the claude.ai UI** — the
proxy no longer appends registry bodies. Do not assume boot ran unless the directive is present.

---

## Dispatch boot profile — `cortex_boot(profile="dispatch")`

A **lean boot for fork / dispatch sessions**. Trims orientation to the dispatch
essentials and injects the packet-named task-class skill **bodies**, so a fork boots
ready to execute one bounded deliverable without re-spending the lead's context.
Mechanism: the unified scoped inject registry (`libs/agent_seat/inject_registry.py`,
`decision:dispatch-boot-profile-shape`) — one declarative registry, one resolver
(`resolve_injected_bodies`) feeding every server inject path.

**When to use**

- Booting a fork/sub-agent against a handoff packet (exactly one bounded deliverable).
- NOT the open-ended lead arc — that uses the full boot (`role="lead"`, no profile).

**Call shape** (`packet_text` carries the packet front-matter + `<invariants>` block)

```
cortex_boot(agent="claude-web", role="lead", profile="dispatch", packet_text="<packet…>")
# views=["dispatch"] is a backward-compat alias for profile="dispatch"
```

**What gets injected** (registry scopes; deduped; must-inline ordered first)

| Scope | Activates when | Bodies |
|---|---|---|
| `DISPATCH_PACKET` | `profile="dispatch"` | each `agent_skill:` id in the packet `<invariants>` block |
| `CODING` | `code_touching=True` (generate path, **not** standard boot) | `orchestrator-workflow`, `architecture-invariants`, `ulg-architecture` |

Standard web lead boot (`profile` omitted): **no** server-side UNIVERSAL/LEAD body inject — attach
required skills in the claude.ai project UI (`cortex-orientation`, `orchestrator-core`, etc.).

Notes:

- **LEAD-scope static inject retired on web/cursor** (2026-07-01): `orchestrator-core` and peers
  are UI-attached, not boot-injected.
- `packet_text` is parsed for `<invariants>` skill ids **only** when
  `profile="dispatch"`. Front-matter `boot_profile: dispatch` + the packet path is
  the dispatch carrier.
- `CODING` bodies do **not** inject on a normal boot (`code_touching=False`); they
  ride the code-touching generate path. Manual preload (§ Tiered preload) stays
  load-bearing for coding regardless.
- Budget: on budgeted paths (hydrated generate) post-dedupe bodies cap at
  `INJECTED_BODY_BUDGET_BYTES` — `critical` tier fails closed, `must_inline` emits a
  `inject:FAIL_CLOSED` marker, `normal` degrades to an index pointer. Web boot passes
  no budget (`budget_bytes=None`), so bodies inline in full.

**Lean dispatch-boot prompt — fork template**

```
cortex_boot(agent="<seat>", profile="dispatch", packet_text="""
---
boot_profile: dispatch
contract: <consult|implement|…>
---
<scope> … </scope>
<invariants>
- agent_skill:<task-class-skill-1>
- agent_skill:<task-class-skill-2>
</invariants>
<task_guidance> … </task_guidance>
<corpus> … </corpus>
<mcp_capabilities> … </mcp_capabilities>
<output_format> … </output_format>
""")
# then: priming checklist — skill_suggest → fetch related threads → load the packet
# <invariants> bodies → execute the ONE deliverable → verify → write the cortex
# sidecar → close back to the dispatch thread (the orchestrator adjudicates/closes).
```

---



## When to boot vs skip

### Full boot — call `cortex_boot`

- Open-ended lead arc (agenda, priorities, unread bus)
- Picking up **agent-bus** threads or needing deadline/todo surfacing
- **`transcript_id`** continuation
- **`principal=`** context
- Inbound handoff expects `cortex_boot_confirmed: true`
- Session will **`session_close`** (needs minted `session_id`)

### Skip boot — bound coding / implement

Skip explicit `cortex_boot` when **all** hold:

1. Task bound — `todo:`, implement packet, or operator prompt with scope + ACs
2. **Skill preload** runs (§ Tiered preload below)
3. Boot agenda not needed (no cross-arc orientation)
4. No `session_close` this chat **or** boot deferred until close

Cursor documents the parallel as **Code Mode — minimal boot** (`agent-surface/sources/boot-protocol.md`).
Web has no separate mode name; same tradeoff applies.

**What skip loses:** `session_id`, briefing card (todos/bus/deadlines/last session), boot skills
index file, operational-context write.

**What skip does not lose (if preload runs):** coding discipline skills — generic web boot does
**not** inline-inject `architecture-invariants` / `ulg-architecture` (`code_touching=False` on
boot inject). Manual preload is load-bearing for ULG coding regardless of boot.

---

## Tiered skill preload (turn 1)

Run after boot (or instead of boot on bound coding). **`md_list` alone is insufficient** — it returns
TOC only; follow with `md_read(section=…)` or `read` / `read_multi`.

| Size / shape | Load |
|---|---|
| ≤ ~5 KB index doc (tag table + deferred refs) | `fs(op="read_multi", …)` |
| Sectional playbook (~6–15 KB) | `md_list` → 2–4 `md_read` sections |
| > ~15 KB or dispatch-only | Defer until trigger; never bulk-read at boot |

**Sandboxes:** one `read_multi` per sandbox (`cortex` vs `workspaces`); mixed bundles need two calls.

### Coding session — minimum slugs

**Full read (workspaces):** `architecture-invariants`, `ulg-architecture`

**Full read (cortex):** `completion-provenance-discipline`, `fs`, `dispatch-shape`

**Sectional:** `git-posture` (Execution lanes, Commit posture, What not to infer),
`service-lifecycle` (Post-code-change loop), `consult-routing` (Executor tier, Implement lane,
Codified bug reports), `implement-work-item`, `modularize-discipline`, `lead-seat-boot`

**Defer:** `handoff-packet-authoring` (44 KB — on dispatch), `friction-review` (bug cycle)

### Life-matter session — minimum slugs

**Full read (cortex, if short):** `fs`, `no-silent-inference`, `named-entity-verification-gate`

**Sectional:** `engagement-stance`, `evidence-review-discipline`, `corpus-cross-reference-discipline`,
`case-evidence-retrieval`, `financial-reasoning`, `lawyer-stance`, `document-ingestion`,
`entity-lifecycle-discipline`, `enrichment-quality-discipline`,
`review-protocol-mandatory-chronology-verification`, `consult-routing`, `lead-seat-boot`

**Domain add-ons (on trigger only):** `hei-application-discipline`, `chase-escrow-discipline`,
`boe19p-appeal-discipline`, `tax`, `w2-ingestion`, etc.

Section titles must match live `md_list` output — bind to TOC, not guessed headings.

---

## skill_suggest after preload

See `skill-suggest-utilization.md` § **Loaded ledger (current contract)** and § **What to pass** (dispatch path: claude-web uses LLM reasoning via worker-hop, Stage-A is the fallback). **Web-consult handoff pickup:** when receiving a `web-consult` / `web-implement` handoff, step-1 `skill_suggest` is mandatory (exception to the boot-resident rule) — see `skill-suggest-utilization.md` § Web → Carve-out.

After preload, call `skill_suggest(loaded=LOADED, conversation_context=…)` and **maintain
`LOADED`** across the session (append each newly fetched slug before the next suggest).

Do not re-fetch slugs in `seat_preloaded` unless verifying digest drift
(typically `cortex-orientation`, `cortex-provenance-discipline`, orientation/opcontext slugs).

---

## Bound coding turn-1 checklist

```
1. [optional] cortex_boot(agent="claude-web", role="lead")  — skip when § bound coding
2. fs(read_multi, workspaces, [architecture-invariants, ulg-architecture])
3. fs(read_multi, cortex, [completion-provenance-discipline, fs, dispatch-shape])
4. md_list + md_read sections for git-posture, service-lifecycle, consult-routing, …
5. skill_suggest(loaded=LOADED, conversation_context="Coding session: …")
6. entity_get(todo:…) → union required_skills onto LOADED when bound
```

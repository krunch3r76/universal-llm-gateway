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
`briefing_card` (+ web invariant bodies). Do not assume that ran unless the directive is present.

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

See `skill-suggest-utilization.md` § **Loaded ledger (current contract)**.

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

# /work-item-review

Post-implementation **R pass** over a **work item** (`todo:` / `a:` / `plan:`) —
the path-sim R leg fired *after ship*, over what was delivered.

```
work item finishes  →  /work-item-review todo:{slug}  →  external verifier critiques the delivery
```

**Path-sim default:** bundled `/path-sim` arcs **auto-fire** this command after Stage-B
ship (path-sim skill § R positions · § Dispatch bindings phase 5). Manual invocation
remains for non-path-sim work items. Skip only the closed set shared with R-admit
(`check_requested=false` / operator no-check, or transport unavailable).

**Substrate (operator bind 2026-07-21 — split from R-admit):**

| Pin | Default substrate |
|---|---|
| R-admit | web-anthropic CDP · Opus 4.8 (staged corpus) |
| **R-after (this command)** | **`seat=cursor-sdk, model=cursor/grok-4.5, contract=light-bounded`** — live checkout |

Independence trade (documented, not hidden): Grok may already have run path-sim Q;
R-after is still ≠ Composer implement and has checkout the web seat lacks. Override
with an explicit model token only when the operator asks.

**This command owns:** *R at after-ship timing, scoped from the work item's own
charter, on the R-after substrate above.* Everything else defers — do NOT re-derive
it here.

| Concern | SOT (defer, ¬ restate) |
|---|---|
| R semantics + **R-after vs R-admit** timeline + substrate split | `path-sim` skill § R positions (+ handshake packet) |
| Review dimensions — Code Review ∪ Discipline (embed by reference) | `review-task-guidance` skill |
| Reflect-axis doctrine — reflect = **external PRM verifier** (not self-signal); G4 distill-trigger | `expand-growth-loop_ws.mdc` |
| xAI coding-lane transport | `consult-routing` skill § xAI coding-substrate; ¬ `xai/grok-*` artisan for checkout review |
| Packet / cursor-sdk dispatch shape | `dispatch-shape` · path-sim Q-only shape as peer |

Provenance: minted from the `expand-growth-loop` charter (agent-bus:5267). Durable home for the two R timeline positions: **`path-sim` skill § R positions**. This command is the **R-after** entry only.

## The delta this command owns

Sibling R/review surfaces scope from a *question* (`/path-sim` R-admit), a
*session* (`/session-review`), or a *diff* (`/diff-review`). This one scopes from
the **work item's charter**: its dense spec, `acceptance_criteria`, and
`files_expected` — so the delivery is pressure-tested against the contract it was
admitted under, across however many sessions produced it.

## Invocation

```
/work-item-review todo:{slug}
/work-item-review a:{assertion_id}
/work-item-review plan:{slug}
/work-item-review todo:{slug} [model]     # optional override — default cursor/grok-4.5
```

**Default:** `cursor/grok-4.5` on cursor-sdk (`contract=light-bounded`). Operator model
token overrides when explicitly supplied. **¬** default to web-anthropic / CDP for
R-after (that seat is R-admit).

## Instructions

### 0. Resolve the work item (scope authority)

```
cortex(tool="entity_get", arguments='{"entity_id":"<work-item-id>","intent":"full"}')
```

Hold: `source_uri` (dense spec), `attributes.spec_sha256`,
`attributes.files_expected`, `attributes.acceptance_criteria`,
`attributes.parent_thread`, the `implement_ready` assertion, and any
`path-sim-{slug}-implement-closeout.md` sidecar.

**Stop** if there is no dense spec / `acceptance_criteria` — nothing shipped to
review; route to `/path-sim` (build it) instead.

### 1. Pin the Question (do NOT invent one)

Compose the scope-lock **using the `path-sim` skill § handshake packet grammar**
(that's the SOT — do not restate the field shapes here). The Question is fixed by
the item:

```
Question: Did the delivery satisfy {work-item}'s acceptance_criteria without
          off-charter drift, and is the end state sound?
Out of scope: re-opening the design (that was the A-bind); new acceptance
          criteria the spec never carried; unrelated dirty-tree files.
```

### 2. Derive file scope (the charter-drift filter — this command's mechanic)

```
DELIVERED = attributes.files_expected ∩ (live changed set since item seed)
```

```bash
git diff --name-only <since-ref-or-item-seed> HEAD
git status --short
```

Partition and report before building the packet:

- `$ON_CHARTER` — in `files_expected` ∧ changed → the review core.
- `$OFF_CHARTER` — changed ∧ ¬ in `files_expected` → **drift finding** (delivered but not declared).
- `$UNDELIVERED` — in `files_expected` ∧ ¬ changed → **acceptance gap** (declared but not delivered).

Apply `/session-review` step 1 auto-exclude / source partition. Apply the
`review-task-guidance` **diff-prohibition** (paths + changed symbols + line counts
in-packet; reviewer reads live files via `fs` / checkout — **available on cursor-sdk**).

### 3. Invariants, manifest, SLOC

Same as `/session-review` steps 2–3 over `$ON_CHARTER` (+ flagged `$OFF_CHARTER`
rows). Reviewer reads live files; no `REVIEW_CODE`.

### 4. Build the packet

Six architecture-handoff blocks. Embed `<task_guidance>` **by reference** from
`review-task-guidance` (`Code Review Dimension ∪ Discipline`) — ¬ inline-copy.
`<scope>` carries: work-item id, charter/parent thread, `spec_sha256`, the pinned
Question + Out-of-scope (§1), the three partitions (§2).
`<mcp_capabilities>`: cursor-sdk / code MCP on (checkout + cortex as needed).

Packet path: `tmp/prompts/work-item-review-{slug}-grok-packet.md`.

Add only the **after-ship R delta** on top of the shared guidance:

```
This is a POST-IMPLEMENTATION R pass; you are the external verifier.
1. Acceptance ledger FIRST: per acceptance_criterion → PASS / FAIL / PARTIAL
   with cited file:line or observed behavior. No locatable evidence = FAIL.
2. Drift: judge each $OFF_CHARTER (justified-declare vs creep) and $UNDELIVERED
   (acceptance-gap vs spec-was-wrong).
3. Critique the DELIVERY under the pinned Question — rank + attach a decisive
   falsifier per critical finding (disposition/falsifier semantics: path-sim
   skill § R positions / handshake).
4. You may widen to structural findings ON the delivered code; you may NOT
   re-open the design (A-bind). Design doubt ⇒ Operation: plan_required.
5. Docstring: run / cite scripts/docstring-quality on $ON_CHARTER public Python —
   criticals=0 or RETURN (path-sim § Docstring in review · R-after).
6. Event instrumentation (when $ON_CHARTER touches behavioral edges or
   @event_factory sites) — Use the event-instrumentation-discipline skill:
   challenge closeout one-liner (events added · "no event warranted (reason)" ·
   prune candidates spotted) AND flag missed log→event / hot-signal prune on
   delivered code. Judgment findings only — ¬ Event Coverage table, ¬ criticals scan.
```

Slug line in packet: `Use the path-sim skill` (R-after pin) · `Use the event-instrumentation-discipline skill` · `Use the docstring-quality skill` · `Use the review-task-guidance skill`.

### 5. Dispatch (R-after substrate)

```
team_dispatch(
  op=generate,
  seat=cursor-sdk,
  model=cursor/grok-4.5,
  contract=light-bounded,
  dispatch_thread_id=<bus thread id or path-sim-{slug}>,
  packet_path=tmp/prompts/work-item-review-{slug}-grok-packet.md,
  skills=[path-sim, review-task-guidance, docstring-quality, event-instrumentation-discipline, cursor-sdk-instruction-standard]
)
```

¬ `model=xai/grok-*` artisan · ¬ default CDP web-anthropic · ¬ `anthropic/*`.
Operator override model token only when explicitly supplied on the command line.

Write durable verdict to
`cortex://notes/system/threads/{slug}-work-item-review.md` (and optional
`tmp/reviews/{slug}-work-item-review.md` mirror).

### 6. Land + close the loop

1. Verdict artifact with acceptance ledger, findings, drift partitions,
   `RATIFY|REVISE|SCOPE-DRIFT`, citing cursor-sdk dispatch id / sidecar URI.
2. **REVISE apply** — `/review-apply` does **not** admit R-after artifacts
   (friction 24952). Apply by:
   - **direct patch** in-seat when the finding carries a safe, scoped edit
     (SEARCH/REPLACE or equivalent) and live files re-validate; or
   - **implement dispatch** (`team_dispatch` / follow-up `todo:`) when the fix
     needs densify, multi-file coordination, or `plan_required`.
3. **G4 distill check** (`expand-growth-loop` SOT): finding class recurred across
   ≥2 work-item reviews ⇒ mint/refine a rule or skill, don't re-catch per item.
4. Seed the outcome:

```
cortex(tool="assert", arguments='{"entity_id":"<work-item-id>",
  "claim":"work-item-review verdict = {RATIFY|REVISE|SCOPE-DRIFT}: {one-line}",
  "confidence":"confirmed","derivation_type":"agent_observation",
  "evidence_uris":["cortex://.../{slug}-work-item-review.md","agent-bus:{thread}"],
  "seeded_by":"cursor"}')
```

## Anti-patterns

| Bad | Good |
|---|---|
| Restate scope-lock / disposition / R semantics here | Defer to `path-sim` skill § handshake + R |
| Scope from `git status` | Scope from `files_expected` ∩ delivery; drift is a finding |
| Re-open the design | Critique the delivery; design doubt → `plan_required` |
| Rubber-stamp | Acceptance ledger with cited evidence + decisive falsifier |
| Fork the shared review body | Embed `review-task-guidance` by reference |
| Re-catch the same finding class every item | G4 distill → mint a rule/skill |
| Default R-after to web-anthropic / CDP Opus | `cursor/grok-4.5` on cursor-sdk (R-admit keeps web Opus) |
| `role=artisan, model=xai/grok-*` for checkout review | `seat=cursor-sdk, model=cursor/grok-4.5` |
| Skip after path-sim Stage-B without closed-set evidence | Path-sim fires R-after by default — run it |
| Silent on event-bearing ON_CHARTER delivery | Challenge closeout one-liner + missed add/prune |
| Route REVISE through `/review-apply` | Direct patch or implement dispatch (24952) |

## Skills

`path-sim` (R semantics SOT) · `review-task-guidance` (dimensions) ·
`consult-routing` (xAI coding-substrate) ·
`docstring-quality` · `event-instrumentation-discipline` ·
`todo-lifecycle` (work-item state).

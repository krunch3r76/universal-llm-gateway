Per-directory code overhaul: modularize, review, fix, docstring, verify.
Orchestrates the full quality pass for a single directory.

## Usage

```
/overhaul {directory}
/overhaul frontier {directory}
```

Where `{directory}` is a path relative to the project root (e.g.,
`services/universal-stargate/systems/proxy/`). When the first argument is
`frontier`, use the automated posture (see Operating posture table); otherwise
use gradual (default).

If you need to unblock a single oversized file first, use:

```
/overhaul-file {file}
```

Then return to `/overhaul {directory}` for the full subsystem pass.

Optional automated path (requires working `team-generate` / Stargate frontier dispatch):

```
/overhaul frontier {directory}
```

Use `frontier` only when frontier dispatch is verified end-to-end. Otherwise stay
on the default gradual posture below.

## Operating posture (default: gradual · autonomous supervised)

The default `/overhaul` run is **autonomous supervised** and **CDP-native** (Jupiter
`team_dispatch(model=cdp/…)` per Use the `claude-ai-cdp-navigation` skill;
`project_ask` = escape only). Cursor orchestrates;
deep-tier reasoning goes to web-anthropic via CDP; Stargate pipelines run only when
the user explicitly approves each call.

**Autonomy bind (operator ratified 2026-07-19):** proceed through green-tier work
without operator stops. Pause only on **yellow** (agent concern) or **red** (high
stakes). Composes with `reasoning-posture` **and** `frontier-reasoning-discipline`:
pin scope at arc birth; declare detent at tier choice; then steelman / calibrate;
`thinking_off ⇏ waive` evidence gates.

**Credit-budget bind (operator ratified 2026-07-20 · agent-bus:5473 ·
`decision:cursor-cdp-credit-budget-policy`):** Cursor lead **orchestrates**;
judgment / split **plans** / reviews / docs burn **claude.ai CDP** (subscription),
not Cursor credits. Checklist SOT:
`cortex://notes/system/threads/5473-credit-budget-checklist.md`.

| Lane | Who | Work |
|---|---|---|
| Judgment / plans / reviews / docs | CDP Opus / Sonnet / Fable via `team_dispatch(model=cdp/…)` | Deep + non-trivial yellow split **plans**, step-4 deep review, Fable legs, step-9/11 arch-doc |
| Execution / gates | Cursor lead (thin) | Birth CHECKPOINT, scan/vulture, thin **apply** of CDP plans, ruff/compileall/`check-imports`, deploy, commit |
| Cap | Manual in-seat | Only obvious yellow ≤2-module **and** ≤2 consumers — else CDP |
| Anti-pattern | ¬ | Lead model (Grok/Sonnet/Opus/Composer/`cursor-sdk`) authoring deep/red split **plans** in-seat; treating Composer apply as “outsourced”; **reverting already-applied package-shadow splits to re-send the same files to CDP** after a mid-arc credit-budget redirect |

**Mid-flight redirect (binding):** If the operator (or a sibling seat) corrects transport mid-arc — CDP vs Manual / credit-budget remind — **keep all applied work**. CDP **only** files that are still red/yellow **and** not yet applied (or whose apply failed audit). **¬** `git checkout` / delete package-shadow / re-plan files whose split already landed. Abort in-flight CDP only when it duplicates an already-applied file; do not abort healthy harvests for remaining work.

**Birth CHECKPOINT (mandatory before step-2 split work):** post the arc birth
CHECKPOINT citing the credit-budget checklist (copy block from that URI). Then
scan. Do not start Manual/Bulk/Deep planning until the cite is on the thread.

| Concern | Gradual (default) | Automated (`frontier` mode) |
|---|---|---|
| Deep file splits | CDP `team_dispatch(model=cdp/opus-5)` modularize packet (Opus; Cursor applies) | `/modularize` (team-generate E2E) |
| Code review | CDP `team_dispatch(model=cdp/opus-5)` review packet (Opus extract/correctness) | `/consult-review` (`code-review` pipeline) |
| Architectural strengthen | CDP `team_dispatch(model=cdp/fable)` **Fable 5** when opportunity scan fires (see below) | same CDP Fable (not Stargate) |
| Architecture doc review | `/review-arch-doc` (CDP default) | `/review-arch-doc team-generate` |
| Bulk split plans | `scripts/modularize plan` — one file at a time; green when plan audits clean | same, batched when user directs |
| Doc generation | step 9 — **CDP Sonnet 5** draft (`team_dispatch(model=cdp/sonnet-5)`; red gate) — **¬** Stargate `doc-generate` | Stargate `doc-generate` (paid API — operator must approve cost) |
| Docstring enhance (§5.6) | CDP Sonnet `team_dispatch(model=cdp/sonnet-5)` (`/docstring-enhance`) — **¬** Stargate API | `/docstring-enhance frontier` (paid API — operator must approve cost) |

### Fable opportunity scanning (standing — operator 2026-07-19)

Along the gradual arc, the lead **looks for chances to strengthen the subsystem
architecturally**, not only to finish hygiene steps. Prefer **Fable 5 via CDP**
(`team_dispatch(model=cdp/fable)`; ¬ API `anthropic/claude-fable-*`;
¬ `cursor/*` Fable) when an opportunity fires.

**Fire Fable (liberally when any match)** after logging a one-line opportunity in
the arc CHECKPOINT:

| Signal | Typical seams |
|---|---|
| Fragile / many-iteration history named in charter or operator prose | control-plane boundaries, admission vs feasibility, queue/backpressure |
| Step-4 Critical/Warning cluster on the same invariant family | eviction busy-matrix, hysteresis, CapacityPool lifecycle |
| Near-ceiling modules that absorb cross-cutting policy | extract vs redesign fork (Fable ranks; ¬ auto-split) |
| G5 `gap` or silent branch without reason fields | event/reason architecture, not log spam |
| Red-tier fork needing ranked binds + falsifiers | Fable = strong-reasoning pass before operator ratify |
| Post-split “correct but brittle” smell | L0∧L1∧L2 path-sim style refine; cascade Fable explores → lesser implements |

**Does not replace:** Opus step-4 extract review · Sonnet step-9 doc projection ·
green mechanical gates. **Structural apply** after Fable harvest remains **red**
(operator ratifies ranked bind). Multi-leg converse + Cowork multitask OK when
Fable fans recon. Log each Fable leg’s `execution_id` + `archive_uri` on the arc thread.

### Three-tier stop model (replaces uniform checkpoint gates)

| Tier | When | Who decides next |
|---|---|---|
| **Green — autonomous** | Scan/vulture (confirmed dead code only), CDP dispatch, apply **audit-pass** split, ruff/compileall/rescan, docstring pass, re-scan | Lead proceeds; log evidence in CHECKPOINT |
| **Yellow — agent concern** | Audit fail, PHANTOM symbols, scope surprise, review Critical, G5 `gap`, import-check fail, ambiguous split boundary | Lead posts **concern block** (fork + evidence + recommended bind); may proceed after self-verify or one-line operator ack |
| **Red — high stakes (rare)** | Event `signal=` renames, cross-subsystem coupling, first live-service deploy touch, step-9 arch-doc draft, Stargate `doc-generate` (frontier only), commit, destructive vulture deletes, admission/coordination paths, **Fable structural architecture binds** | **Strong reasoning pass first** (CDP **Fable 5** for architecture forks; CDP Opus for extract/review Criticals; or in-seat Opus-class bind) → operator ratification only after that pass surfaces the fork |

**Red is rare.** Default: classify as yellow and resolve in-seat. Escalate to red only
when the fork is irreversible, cross-subsystem, or deploy-touching. Red MUST NOT
surface to the operator as a raw pause — run a strong-reasoning review (CDP
`team_dispatch(model=cdp/fable|cdp/opus-5)` or equivalent) that returns a ranked bind + falsifiers; operator
sees that synthesis, not an undigested gate.

**Yellow concern block** (copy into CHECKPOINT or agent-bus sidecar):

```
CONCERN
tier: yellow
fork: <what decision is blocked>
evidence: <file:line | probe | audit item>
recommended: <proceed | escalate-red | rework>
note: <≤25 words>
```

**Mapping old steps → tiers:**

| Step block | Default tier |
|---|---|
| 1–1.5 scan + vulture | Green (vulture deletes: green only when confidence ≥80 and not dispatch/handler) |
| 2 plan + audit | Green when audit PASS; Yellow on fail |
| 3 apply split | Green when plan audit PASS |
| 4 review + triage | Yellow on Critical; Green on clean; G5 probe before trusting claims |
| 5–8 docstring/gates/rescan | Green |
| 9 arch-doc draft (CDP Sonnet 5) | Red |
| 10–11 arch doc review | Yellow on Critical; Red before replacing `docs/architecture/*.md` |
| 12 commit | Red |

**CHECKPOINT cadence:** post at wave boundaries and after any yellow/red resolution —
not after every green step. Include tier log (what ran autonomous vs what paused).

**CDP dispatch invariant**: substantive content lives in sealed prompt files
(`tmp/modularize-plans/`, `tmp/reviews/`) and preferably cortex-staged corpus
(`cortex://notes/system/threads/{arc-slug}/source/`) for fewer tool calls.
Invoke via `team_dispatch(model=cdp/…)` (`sidecar_ref` / `packet_path` after
staging); stage per Use the
`claude-ai-cdp-navigation` skill (`workspaces://` is readable — package hot paths
anyway). **Escape only:** bare `project_ask` when `team_dispatch` CDP unavailable
or `purpose=` inject required. Legacy `agent_bus` pointer posts remain valid when MCP is unavailable.

**Approval vs transport** (operator bind 2026-07-19; autonomy ratified 2026-07-19):

| Action | Operator confirm required? |
|---|---|
| Green-tier steps (see three-tier model) | **No** — proceed autonomous; log in CHECKPOINT |
| Yellow-tier concern | **Optional** — one-line ack or override; lead may self-resolve when evidence clears |
| Red-tier fork | **Yes** — only after strong-reasoning pass (CDP Fable for architecture; CDP Opus for extract Criticals; or in-seat Opus-class bind) |
| **CDP dispatch** (`team_dispatch(model=cdp/…)` for deep split plan, step-4 review, **Fable opportunity legs**, **step-9 Sonnet draft**, step-11 arch-doc) | **No** — once arc is live + red gate cleared for step 9, orchestrator fires CDP without per-dispatch ask; Fable legs auto when opportunity scan fires (operator may pre-authorize liberally per arc) |
| Stargate pipeline calls (`modularize plan`, `code-review`) | **Yes** — each invocation |
| Stargate `doc-generate` | **Gradual: banned.** **Frontier only** — each invocation (paid Sonnet+Gemini API ≈ $1–2/run; operator must approve cost) |
| Step 3 split apply | **No** when plan audit PASS (green); yellow/red per tier table |

CDP is default overhaul transport; it is not an optional side-path gated separately from the arc.

## Instructions

Execute these steps in order. Each step must complete before moving to the next.
Honor the **three-tier stop model** above. Green-tier: proceed without operator pause.
Yellow: emit concern block. Red: strong-reasoning pass, then operator ratification.
Step 4 manages its own triage flow per the chosen review path.

### 1. Scan for SLOC violations

```bash
source ~/.venvs/universal/bin/activate
scripts/modularize scan {directory}
```

Note any red (>400) or yellow (301-400) files.

### 1.5. Scan for cross-file dead code

```bash
vulture {directory} vulture_whitelist.py --min-confidence 80
```

Review findings. Delete genuinely dead code (unused functions, unreachable
branches, dead exports) before proceeding — removing dead code first means
the pipelines analyze cleaner files and don't waste tokens on code about
to be deleted.

Known false positives: `getattr()` dispatch, FastAPI route handlers, event
handler callbacks, `__init__.py` re-exports. Add confirmed false positives
to `vulture_whitelist.py`.

### 2. Generate split plans for oversized files

For each file flagged red or yellow in step 1, choose a tier. In gradual mode,
process **one file at a time**. **¬** ask before CDP `team_dispatch(model=cdp/…)` (credit-budget
bind — CDP is default transport). **Do** ask before each Stargate Bulk
`modularize plan` invocation.

| Tier | Tool | When to use |
|---|---|---|
| **Manual** (narrow exception) | Cursor lead thin split (read + apply) | Yellow (301–400) **only** when simple structure **and** ≤2 consumers **and** ≤2-module package-shadow — else escalate to Deep CDP. **¬** Manual for red (>400). **¬** in-seat lead inventing deep plans (Grok/Composer/`cursor-sdk` as planner). |
| **Bulk** | `scripts/modularize plan {file}` (Stargate `modularize` pipeline) | File ≤600 SLOC, simple consumer graph — **user approves each `plan` invocation** |
| **Deep** (gradual default) | CDP `team_dispatch(model=cdp/opus-5)` modularize (see §2.1) | Default for **all red**; yellow that fails Manual caps; Bulk coverage warnings / PHANTOM / complex consumers; file >600 SLOC |
| **Deep** (`frontier` mode only) | `/modularize {file}` (team-generate E2E) | Same triggers as deep CDP — use only under `/overhaul frontier` |

The bulk tier runs the modularize pipeline (analyze → critique → finalize) via
Stargate (`POST /v1/chat/completions`), model `modularize`. Cheap but lacks live
consumer reads. **Do not batch** bulk plans across files in gradual mode unless
the user explicitly requests it.

The deep tier (gradual) submits a six-block packet via CDP `team_dispatch(model=cdp/opus-5)` (§2.1;
Opus). CDP plans; **Cursor thin-applies** the harvested plan (no frontier Phase 2
auto-execution). Lead model identity (Grok, Sonnet, …) does **not** change this —
the lead orchestrates and applies; it does not replace CDP as the split planner.

If unsure in gradual mode: **Deep CDP** for red and for any yellow that is not an
obvious ≤2-module / ≤2-consumer Manual. Bulk only when operator approves each
`plan` call. **¬** default to Manual “because the lead is Grok / in Cursor.”

**Anchoring caveat — ¬ scaffold→densify here.** The packet-scaffolding pattern
(cheap tier drafts a scaffold, reasoner densifies — see
`handoff-packet-authoring.md` § Preliminary scaffold → densification) does NOT
apply to split planning. A preliminary "split skeleton" embeds **module boundary
design judgment**; a cheap draft can anchor the deep-tier reasoner into elaborating
a flawed decomposition instead of re-deriving boundaries from the live consumer
graph. Keep the tiers **either/or with escalation** (bulk *or* deep web-claude,
escalate on coverage warnings / PHANTOM symbols), ¬ chain a Composer-drafted plan
into a web-claude densification pass. Composer scaffolding is reserved for the
*review/doc-review packets* (steps 4, 11), not the split plan itself.

### 2.1. Deep tier — CDP modularize handoff (gradual default)

When bulk is insufficient or skipped, build and submit a modularize packet via
**`team_dispatch(model=cdp/opus-5)`** (Use the `claude-ai-cdp-navigation` skill;
`project_ask` = escape only) instead of
calling `/modularize` or waiting on manual operator push:

1. Gather artifacts per `/modularize` §2.1–2.2 (source, consumers via grep, composed `<invariants>`, `<architecture>` replacement table for violations in this file).
2. **Skill delivery (BINDING — claude.ai):** modularize plans need architecture
   context **inlined into the sealed prompt**. These are **not** Claude
   Customize Skills slugs (`architecture-invariants` + `modularize-discipline`
   are life_local; `ulg-architecture` is cursor_only) — **¬ slash-inject them**
   (silent no-op).

   **Inline into `<invariants>` (required):**
   | Content | Why |
   |---|---|
   | `architecture-invariants` | Universal floor — quality/SLOC, transport, events, no-bc |
   | `modularize-discipline` | Split rules — forbidden names, package-shadow, consumer surface |
   | `ulg-architecture` `[ulg:*]` floor | When target is under `services/` / `libs/` in this repo |

   Paste short excerpts (tag index + rules that bind this file). Optional cortex
   stage of full skills under `cortex://notes/system/threads/{arc-slug}/skills/`
   is backup only — **does not** replace inline. **¬** cite retired
   `cortex://agent-skills/*.md`. **Fail closed** if the sealed prompt lacks
   inlined architecture-invariants + modularize-discipline before submit
   (`ulg-architecture` excerpt required for ULG `services/` / `libs/`).

   Optional shared_sync chip helpers (real Claude slugs, when on chip):
   `/frontier-reasoning-discipline`, `/evidence-review-discipline`,
   `/no-silent-inference` — additive only; ¬ substitutes for the three inlines.
3. Stage corpus under `cortex://notes/system/threads/{arc-slug}/source/` (source file, consumer manifest). See Use the `claude-ai-cdp-navigation` skill § web-anthropic-cdp dispatch constraints.
4. Write `tmp/modularize-plans/{sanitized-name}-packet.md` — six-block format from `architecture-handoff-protocol.mdc` (same block table as `/modularize` §2.3). Required skill **inlines** live inside `<invariants>` (fleet rule — Use the `claude-ai-cdp-navigation` skill).
5. Submit via `team_dispatch(op=generate, model=cdp/opus-5, contract=light-bounded, packet_path=tmp/modularize-plans/…, dispatch_thread_id=<arc-thread>)` — wait via `poll_hint` / `agent_bus.wait` until `archive_uri` present. **Escape only:** `project_ask(op=submit, …)` then poll via `project_ask(op=poll, execution_id=<id>)`. **NEVER curl localhost :8765** (web-fetcher) for `/v1/project-ask/*` — MCP poll only on escape path (cdp-ask is :8770 via `PROJECT_ASK_URL`).
6. **Wait for harvest (bounded).** After CDP submit, wait via `poll_hint` — or MCP `project_ask(op=poll, …)` on escape path only — not curl/REST — until `archive_uri` is present **or** the **lead wait budget** elapses, whichever comes first.
   - **Lead wait budget:** wall-clock **420 seconds** from submit (`N` — **provisional-v0**). This is **separate from** satellite `timeout_s` (idle semantics pause during active Opus — see Use the `claude-ai-cdp-navigation` skill § Idle vs in-flight).
   - **Dogfood calibration:** record wall-time-to-`archive_uri` on deep-tier dispatches; if median healthy harvest over **≥5** dispatches exceeds **N**, raise **N** (or promote deferred SLOC-tiering per Q11).
   - Today's poll surface does not expose tool progress — treat `status=running` as inconclusive until harvest or budget expiry.
   - **Do not re-submit** the same modularize packet unprompted (24911 anti-redispatch applies to any recovery path).

7. **On harvest (`archive_uri` present):** Audit the returned plan per `/modularize` §2.6 and `modularize-discipline`. **Green** when audit PASS → proceed to step 3; **yellow** on fail (concern block). Plans harvested without architecture-invariants + modularize-discipline **inlined** in the sealed prompt start **yellow** — re-audit forbidden names and SRP before apply; ¬ treat as automatic green.

8. **On lead wait budget expiry without `archive_uri`:** Execute **in-seat fallback** (do not treat as overhaul failure):
   a. **Plan:** Lead reads source + consumers; produce a **line-range package-shadow split plan** per `/modularize` apply path (reference `/modularize` §2.6 and `modularize-discipline` — do not restate layout rules inline).
   b. **Minimal audit:** Before apply, verify the §2.6 / `modularize-discipline` checklist (forbidden module names, package-shadow layout, public import surface preserved for consumers grep-found in step 1, cross-module import violations, logger anti-patterns, proposed modules ≤300 SLOC target via scan estimate).
      - **Runtime gate (green path):** run `scripts/check-imports` on affected packages (or unit tests touching the split module) — must pass before **green** apply; failure → **yellow CONCERN**, never silent green.
      - **Pass** static + runtime → **green** apply with CHECKPOINT note (below). **Any static fail, runtime fail, or ambiguous boundary** → **yellow CONCERN** block before apply.
   c. **Re-poll before abort:** Immediately before abort, **re-poll `archive_uri` once**. If present → cancel fallback; audit CDP plan per step 7; proceed on tier. If still absent → apply in-seat plan per tier rule from (b), **then** abort hygiene:
      - `project_ask(op=abort, execution_id=…)` on the in-flight dispatch (**escape path only**).
      - Poll until `status=aborted` confirmed.
      - **Do not re-submit** the same packet; **do not** new `--register` to "replace" the execution (Use the `claude-ai-cdp-navigation` skill § Anti-redispatch).
   d. **CHECKPOINT note (required on expiry path):**
      ```
      cdp_wait_budget_expired
      execution_id: <id>
      registration_id: <id if known>
      elapsed_wall_s: >=420
      lead_wait_budget: provisional-v0 (N=420)
      fallback: in-seat line-range split
      harvested_after_abort: yes|no
      re_poll_archive_uri: absent|present
      tier: green|yellow (per audit)
      ```
   e. Proceed to step 3 apply when audit tier permits.

For execution-phase iteration, submit a follow-up `team_dispatch(model=cdp/opus-5, …)` with `<prior_pass>` context
rather than opening a redundant parallel dispatch.

Under `/overhaul frontier`, replace steps 1–8 above with `/modularize {file}`.

### 2.5. Pipeline model configuration

The bulk pipeline's `plan_model` and `execute_model` live in
`pipelines/modularize/models.yaml`. The current values are tuned defaults; do
not auto-update them as part of overhaul. Update them through a focused
consultation cycle on the `pipelines/modularize` subsystem when a clearly
stronger candidate is empirically demonstrated on this codebase, not from a
public leaderboard.

The deep tier under `/overhaul frontier` (`/modularize`) hardcodes `openai/gpt-5.5` per
`projects/.cursor/rules/handoff-dispatchers.mdc` and is not affected by `pipelines/modularize/models.yaml`.
The web-claude deep tier is not affected by `models.yaml` either.

If a service restart is required after editing pipeline model configs, ask the
user to run `./manage` — do not start/stop services directly.

Present each plan to the user for review.

For each oversized file plan, also show a proposed package-shadow directory
tree before requesting step 3 approval.

Example format:

```text
path/to/target/
  __init__.py
  module_a.py
  module_b.py
```

Requirements:

- Show one tree per planned split target file.
- Include all planned submodules and `__init__.py` re-export surface.
- Mark uncertain symbol placement explicitly; resolve with user before edits.

### 3. Apply split plans

For each approved split plan, implement the module extraction:

- Default to **package-shadow** layout for file splits:
  - `path/to/target.py` -> `path/to/target/` package directory
  - split modules live under `path/to/target/*.py`
  - `path/to/target/__init__.py` re-exports preserve the public import surface
  - remove original `path/to/target.py` after package exports are in place
- Create the new module files as specified
- Move definitions to their target modules
- Update imports across all consumers
- Create `__init__.py` with re-exports if needed

If a generated plan has coverage warnings, PHANTOM symbols, or is otherwise
underspecified, **escalate to the deep tier** instead of asking the user to
approve a partial bulk plan:

- **Gradual (default)**: web-claude handoff per §2.1
- **`frontier` mode**: `/modularize {file}`

Do not approve and apply a partial bulk plan when deep escalation is indicated.

For plans that look complete and consistent, proceed with apply under **green** tier
(audit PASS). **Yellow** when audit fails or scope is ambiguous.

**Dispatch (default, when applying from a non-Cursor seat):** `team_dispatch(op=generate, seat=cursor-sdk, packet_path=tmp/reviews/{unit}-implement-packet.md, contract=implement, dispatch_thread_id={arc-id})` — auto Composer, no IDE pickup. The split-apply packet MUST be **dense** (every move/re-export/consumer-update pinned; Composer executes mechanically). Use `cursor-implement` handoff only when the SDK worker is unavailable or operator-attended IDE execution is explicitly wanted. Full policy: `docs/agent-guides/skills/consult-routing.md` § Implement lane (cortex SOT: `agent-skills/consult-routing.md`).

### 4. Code review (choose path)

**Gradual (default) — tiered review transport**

Scope files in `{directory}`:

```bash
git diff --name-only -- {directory}
find {directory} -name '*.py' -not -path '*/__pycache__/*' | sort
```

**Event coverage + G5 (binding — before triage):** Run an `EVENTS-PROBE` pass per
`path-sim` § Events/gap probe (grammar SOT:
`cortex://notes/system/threads/ulg-path-sim-events-g5-densify.md`). Map review
findings to expected signal families using
`cortex://notes/system/threads/ulg-path-sim-events-g5-v1-implement-densify.md`
(overhaul step-4 event map). §4.5 covers observability noise policy; G5 covers
evidence verification — do not fork grammar into this command. `/overhaul` does
**not** invoke `/path-sim`; it **references** the same probe block at step 4 closeout.

| Tier | Trigger | Transport |
|---|---|---|
| **Deep / cross-subsystem** | Multiple changed packages, external callers, or yellow/red scope per three-tier model | CDP `team_dispatch(model=cdp/opus-5)` bus-nudge — **answer-3 preflight (reject-incomplete):** stage cortex corpus under `cortex://notes/system/threads/{arc-slug}/…` with **every external caller** of each changed public symbol + omission-disclosure for unstaged paths the verdict would need; build six-block packet to `tmp/reviews/overhaul-{subsystem}-cdp-review-packet.md`; **required** ≤25-line bus pointer on arc coordination thread (URI table only); `team_dispatch(op=generate, model=cdp/opus-5, sidecar_ref=cortex://…, dispatch_thread_id=…)`, wait via `poll_hint` until `archive_uri`; **escape:** `project_ask` submit+poll when CDP team_dispatch unavailable; **do not** use `team_dispatch` handoff + manual operator push (friction a25444) |
| **Narrow / single-subsystem** | Single package, ≤2 consumers, green tier | In-seat Grok High or `team_dispatch(op=generate, seat=cursor-sdk, contract=light-bounded)` on staged diff — no open web thread, no push reminder |

**Deep tier packet** — six-block; skill delivery per Use the `claude-ai-cdp-navigation`
skill § Skill delivery — fleet rule:

- **Inline** into `<invariants>` (not Claude slugs): `architecture-invariants` +
  `ulg-architecture` tag floors relevant to changed code. **¬ slash.**
- **Claude-slug engage** at top of sealed prompt (`/` line or `Use the … skill`):
  `evidence-review-discipline`, `frontier-reasoning-discipline`,
  `no-silent-inference` (on Customize staging).
- Also: `<scope>`, `<corpus>` (changed manifest + staged excerpts),
  `<task_guidance>` (post-overhaul split review — correctness, invariants, event
  gaps, docstring quality, §4.5 noise), `<output_format>` (severity-grouped
  findings with `Evidence:`). Prefer cortex-packaged corpus per CDP skill
  (speed/targeting; `workspaces://` readable when exploration is named).
  **Fail closed** if required inlines missing before submit.

**Lead sequence (deep tier, binding):** answer-3 preflight PASS → stage → **required** thin bus pointer on arc thread →
`team_dispatch(model=cdp/opus-5)` submit **before** turn close → suppress push reminder (24628) → wait
harvest → triage into Applied / Pending / Rejected / Suggestions.

Triage findings: apply Critical only after validation; yellow concern block on
ambiguous scope. Alternatively, `/diff-review` when manifest workflow fits better.

**CDP-down (deep tier):** halt with `BLOCKED:applied-unreviewable` if code already
applied; report on arc thread — ¬ silent fallback to `team_dispatch` handoff.
Legacy web-claude handoff only when operator explicitly chooses fallback after CDP-down report.

**`frontier` mode — pipeline review**

Execute the full `/consult-review` workflow on the directory's Python files.
This handles pre-flight pipeline availability checks, SLOC gates, batching,
invariant validation, event coverage gap detection, and the closing checklist.

Use the same scope commands as above, then follow `/consult-review` instructions.

The closing checklist (Applied / Pending / Rejected / Suggestions) replaces
manual finding triage. All fixes require user approval — do not auto-apply
Warning or Suggestion items.

Event coverage gaps surfaced by the review pipeline are handled within
`/consult-review` (see its Event Coverage Gaps → Suggestions section).
New signals follow `docs/event-contracts.md` conventions.

### 4.5. Observability-first noise reduction

Apply this policy across touched files during overhaul:

- Lean on structured events for request-path observability; avoid verbose
per-request/per-candidate logs at `info` level when event coverage exists.
- Demote repetitive branch diagnostics to `debug`; keep `info`/`warning` for
actionable boundaries and operator-relevant summaries.
- Preserve one concise summary log per major success/failure branch where
useful for quick local triage.
- If a noisy log is reduced or removed, ensure equivalent (or better) event
signal coverage remains.

When generating/applying review fixes, treat these as high-value event
opportunities before introducing new logs:

- Decision/branch outcome events with explicit reason fields
- Retryable vs non-retryable failure boundary events
- Queue/backpressure lifecycle events (enter/wake/timeout/cancel)
- Spillover/fallback/handoff transition events
- Invariant guard-block events (condition blocked action)
- Recovery/unblocked events after prior failure states

### 5. Docstring pass

Use the `docstring-quality` skill (canonical slug — seat self-fetches).
Ensure every module, class, and public function meets that bar before step 5.5.
Step 9 projects docstrings into the architecture doc — thin docstrings produce
thin architecture docs.

#### Quality standard

**Module docstrings** (≥15 words): What the module does, who calls it, key
invariants or design decisions.

Good:

```
"""Request routing and gateway selection for federated inference.

Implements the core routing algorithm that selects which gateway should
handle an incoming request. Called by the proxy layer after model ID
resolution. Routing decisions are based on model availability, capacity
constraints, and latency preferences.

Invariant: never routes to a gateway that lacks the requested model or
has exhausted its capacity budget.
"""
```

Bad:

```
"""Request routing module."""
```

```
"""Handles routing."""
```

**Class docstrings** (≥15 words): Purpose, lifecycle (how/when created
and destroyed), key methods worth knowing about.

Good:

```
"""Tracks in-flight requests and enforces per-gateway capacity limits.

Created once per Stargate instance at startup. Maintains a concurrent
map of active requests keyed by (gateway_id, model_id). Capacity is
released when the request completes or times out.

Key methods:
    acquire(): Reserve a capacity slot, blocking if at limit.
    release(): Free a capacity slot after request completion.
    snapshot(): Return current utilization for telemetry.
"""
```

Bad:

```
"""Capacity tracker."""
```

```
"""Class for tracking capacity."""
```

**Function docstrings** (≥10 words): What the function does (not just
restating the name), parameter semantics when non-obvious, return value,
side effects (event emissions, state mutations, I/O).

Good:

```
def resolve_model_id(raw_id: str, aliases: dict[str, str]) -> ModelId:
    """Normalize and resolve a raw model identifier.

    Handles alias expansion, version suffix stripping, and quantization
    tag normalization. If raw_id matches an alias key, the alias target
    is used before normalization.

    Returns a validated ModelId or raises ModelIdError if the format
    is unrecognizable after all normalization attempts.
    """
```

Bad:

```
def resolve_model_id(raw_id, aliases):
    """Resolve model ID."""
```

```
def resolve_model_id(raw_id, aliases):
    """Resolves the model id from raw id and aliases."""
```

#### Audiences

Written for three consumers:

1. **Humans** reading code — explain the "why", not just the "what"
2. **Agents** navigating the codebase — name the callers, invariants,
  and relationships so an LLM can reason about dependencies
3. **Embedding models** chunking for RAG — use distinctive terms that
  differentiate this module/class/function from similar ones

#### Scope

Skip private helpers (`_name`) unless their logic is non-obvious.
For `__init__.py` files, a brief re-export summary is sufficient.

### 5.5. Verify docstring quality

Run the docstring quality checker:

```bash
source ~/.venvs/universal/bin/activate
scripts/docstring-quality scan {directory}
```

This checks every module, public class, and public function for:

- **empty** (critical): No docstring at all
- **too_short** (warning): Below word count threshold for scope
- **name_echo** (warning): First sentence just restates the element name

If there are critical issues (exit code 1), fix them and re-run before
proceeding. For warnings, review the report and improve any docstrings
that would produce thin architecture doc sections.

The goal: every docstring should give step 9 enough material to
write a substantive architecture doc paragraph, not just a label.

### 5.6. Docstring enhancement pass (gradual default — CDP Sonnet 5)

When local/manual cleanup still leaves thin content (**warnings** that would
starve step-9 arch-doc projection), run CDP Sonnet enhance — **not** the
Stargate API pipeline:

```
/docstring-enhance {directory}
```

Use the `claude-ai-cdp-navigation` skill. Template:
`cortex://notes/system/templates/cdp-overhaul-docstring-enhance.md`.
Apply harvest via `scripts/docstring-apply`; re-run step 5.5 until criticals
are 0 and feedstock is thick enough for step 9.

**Forbidden on gradual:** `/docstring-enhance frontier` / curl `model=docstring-enhance`
(paid Stargate API). Frontier override only with explicit operator cost approval.

Use when:

- warnings remain concentrated on module/class/function quality (not missing files)
- prior arch drafts produced weak sections or repeated HUMAN markers
- credit-budget bind: burn Claude **subscription**, not API credits

After apply + step 5.5 green on criticals, proceed to quality gates / step 9.

### 6. Quality gates

```bash
source ~/.venvs/universal/bin/activate
ruff check --select=UP --fix {directory}
ruff format {directory}
python -m compileall -q {directory}
ruff check {directory}
```

**Import resolution (mandatory).** `compileall` checks syntax only — it does
not execute imports. After any split, package-shadow move, or relative-import
change under `services/universal-stargate/`, run:

```bash
scripts/check-imports --stargate-entry {directory}
```

For `libs/` changes:

```bash
scripts/check-imports libs/{package}/
```

When `{directory}` touches Stargate source, also verify the service entry
point loads (included automatically by `--stargate-entry`):

```bash
# imports systems.proxy.app — same path as start_proxy.py
```

`quality_gate(files=[...])` runs the same import check when MCP is available.
**Invariant:** compileall pass ≠ imports pass. Do not commit or call
`sync_restart stargate` until `check-imports` exits 0.

For Stargate-touching changes, run post-apply (after step 3 or step 6):

```bash
manage(action="sync_restart", service="stargate")
manage(action="wait_healthy", service="stargate", timeout=120)
```

Per `service-lifecycle` skill — source under `services/universal-stargate/`
requires deploy verification, not just static gates.

### 7. Unused imports

```bash
ruff check --select F401 {directory}
```

Fix any remaining unused imports.

### 8. Re-scan

```bash
scripts/modularize scan {directory}
```

Verify all files are green (≤300 SLOC).

For any file still yellow/red after the bulk pass, **escalate to the deep tier
per file**:

- **Gradual (default)**: web-claude handoff per §2.1
- **`frontier` mode**: `/modularize {still-red-file}`

Do not loop the bulk pipeline a second time on the same file — if it didn't
land cleanly the first time, escalate to web-claude (or `/modularize` under
`frontier` mode). Re-run step 8 after each deep split completes; continue until
all files are green or the user explicitly defers a remaining violator.

### 9. Generate/update architecture doc (gradual default — CDP Sonnet 5)

> **Scope of this arc (non-goal):** the draft re-projects what the source
> *declares* (docstrings, signatures, imports); it does not read function bodies
> and does not attest behavior. Bug discovery belongs to steps 1.5 (vulture),
> 4 (body-reading review), and 5 (writing honest docstrings). A "verified" arch
> doc is NOT a behavioral attestation.

**Docstring feedstock gate (BINDING — fail closed):** before step-9 submit:

```bash
scripts/docstring-quality scan {directory}
```

- **Criticals > 0** → halt; finish §5 / §5.6 (CDP Sonnet enhance) until criticals
  are 0. **¬** draft arch-doc from empty docstrings.
- **Warnings concentrated** (too_short / name_echo on public surface that step-9
  will project) → run §5.6 CDP enhance (or explicit operator waive with CHECKPOINT
  note). Skipping thicken underfeeds the managed arch doc and breaks the flow.

**Gradual gate (red):** summarize steps 1–8 outcomes (include docstring scan
summary) and ask the user before firing the CDP Sonnet draft. Skip this step
entirely if the user defers architecture doc work.

**Gradual transport (BINDING):** Jupiter CDP `team_dispatch(model=cdp/sonnet-5)`
(Claude.ai subscription). Prefer `team_dispatch(model=cdp/…)`; `project_ask` =
escape only (SOT: `consult-routing` § Surface gate). Use the
`claude-ai-cdp-navigation` skill.

**Forbidden on gradual:** Stargate `doc-generate` / `curl … model=doc-generate`
(paid `anthropic/*` + Gemini API — historically ~$1–2 and ~6 min per run).

**`frontier` mode only:** Stargate `doc-generate` remains an explicit paid
override. Before invoking it, warn the operator of API cost and get approval;
then use the curl recipe under §9b below.

#### 9a. Gradual — CDP Sonnet 5 draft

1. **Stage cortex corpus** under
   `cortex://notes/system/threads/{arc-slug}/source/` (prefer packaging for
   fewer tool calls; `workspaces://` is readable — name it when exploration
   is encouraged):
   - Module inventory from `scripts/modularize scan {directory}` →
     `…/source/doc-scan-summary.txt`
   - Key source files / docstring excerpts covering claimed areas →
     `…/source/` (or `doc-source-manifest.txt` listing staged URIs)
   - Existing arch doc if present →
     `…/source/docs/architecture/{subsystem}.md` (mirror)
   - **No paid extract call** — lead-built inventory only

2. **Seal packet** to `tmp/reviews/overhaul-{subsystem}-doc-draft-packet.md`
   from template `cortex://notes/system/templates/cdp-overhaul-doc-draft.md`
   (six-block shape; prefer cortex URIs for the hot path).

3. **Submit + wait** (prefer team_dispatch CDP — NEVER curl `:8765` for project-ask):

```
team_dispatch(
  op="generate",
  model="cdp/sonnet-5",
  contract="light-bounded",
  packet_path="tmp/reviews/overhaul-{subsystem}-doc-draft-packet.md",
  # or sidecar_ref="cortex://…" after staging the sealed packet
  dispatch_thread_id="<arc-thread>"
)
# → poll_hint; wait until archive_uri is set
```

**Escape only:** `project_ask(op=submit, …, model=sonnet-5)` →
`project_ask(op="poll", execution_id="<id>")`.

4. **Materialize draft artifacts** from harvest / archive:

```bash
SUBSYSTEM="$(basename "{directory%/}")"
DOC_PATH="docs/architecture/${SUBSYSTEM}.md"
# Write harvested architecture markdown (lead extracts from archive_uri body):
#   → ${DOC_PATH}.generated
# Shim for §10–11 /review-arch-doc (empty arrays OK if harvest has no inventory):
cat > /tmp/doc-generate-result.json <<'EOF'
{
  "unsupported_claims": [],
  "missing_coverage": [],
  "human_markers": [],
  "review_notes": ["cdp-sonnet-5 draft; not Stargate doc-generate"],
  "inventory_sha": "cdp-sonnet-draft"
}
EOF
```

If CDP fails or harvest is incomplete: **halt** — report to operator.
¬ fall back to Stargate `doc-generate` on the gradual path.

#### 9b. Frontier override — Stargate `doc-generate` (paid API)

Only under `/overhaul frontier` **and** after operator cost approval:

```bash
curl -s http://localhost:9999/v1/models | jq '.data[] | select(.id == "doc-generate")'
DIRECTORY_ABS="$(realpath "{directory}")"
DOC_GEN_RESPONSE="$(curl -s -X POST http://localhost:9999/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"doc-generate\",\"messages\":[{\"role\":\"user\",\"content\":\"${DIRECTORY_ABS}\"}]}")"
echo "$DOC_GEN_RESPONSE" > /tmp/doc-generate-response.json
DOC_JSON="$(echo "$DOC_GEN_RESPONSE" | jq -r '.choices[0].message.content // empty')"
echo "$DOC_JSON" | jq . > /tmp/doc-generate-result.json
SUBSYSTEM="$(basename "{directory%/}")"
DOC_PATH="docs/architecture/${SUBSYSTEM}.md"
echo "$DOC_JSON" | jq -r '.doc_markdown' > "${DOC_PATH}.generated"
```

If extraction/parsing fails: save `/tmp/doc-generate-response.json`, report,
do not continue to commit until step 9 succeeds.

### 10. Review generated doc

Review `${DOC_PATH}.generated` before replacing `${DOC_PATH}`:

1. Verify every factual claim traces to extracted docstrings/signatures /
   staged corpus.
2. Resolve all `unsupported_claims` and `missing_coverage` items from
  `/tmp/doc-generate-result.json` (may be empty for CDP Sonnet drafts).
3. Address each `<!-- HUMAN: ... -->` marker with authored content where needed.
4. Ensure generated sections remain wrapped by:
  - `<!-- GENERATED:START -->`
  - `<!-- GENERATED:END -->`
5. Preserve authored sections marked by `<!-- AUTHORED -->` where applicable.

When review passes:

```bash
mv "${DOC_PATH}.generated" "${DOC_PATH}"
```

### 11. Architecture doc review (gradual default — CDP)

Run `/review-arch-doc` on the generated architecture doc. **Gradual mode omits
the dispatcher argument** — `/review-arch-doc` defaults to **CDP-native**
`team_dispatch(model=cdp/opus-5)` (not legacy web-claude handoff + manual push).

```bash
/review-arch-doc "${DOC_PATH}" /tmp/doc-generate-result.json --source {directory}
```

Lead stages cortex corpus (**answer-3 preflight:** source coverage for **every area the doc asserts** + omission-disclosure; reject-incomplete), fires CDP bus-nudge with **required** ≤25-line bus pointer on arc coordination thread, polls harvest. Follow
`/review-arch-doc`'s validation contract before applying any finding:

1. Apply Critical findings that survive local rule validation.
2. Ask the user before applying Warning or Suggestion findings.
3. Reject findings that contradict the extraction inventory or workspace rules.
4. Record surfaced/deferred findings in the review artifact.

**`frontier` mode** — synchronous automated review when frontier dispatch is verified:

```bash
/review-arch-doc team-generate "${DOC_PATH}" /tmp/doc-generate-result.json --source {directory}
```

If `team-generate` fails, do not silently fall back. Offer the user:

- retry later (Stargate may recover)
- continue with CDP default (gradual)
- `local-self` (in-context review, no external dispatcher)
- legacy `web-claude` handoff only after explicit operator fallback when CDP unavailable

Proceed only with the user's chosen path.

### 12. Commit code + docs together

Stage both code and architecture doc updates in one commit:

```bash
git add {directory} "${DOC_PATH}"
git commit -m "overhaul: {directory} (code + architecture doc)"
```

Do not split code and doc updates into separate commits.

## Rules

- Default posture is **gradual / autonomous supervised**; use `/overhaul frontier` only
  when team-generate is verified end-to-end
- **Credit-budget (5473):** birth CHECKPOINT cites
  `cortex://notes/system/threads/5473-credit-budget-checklist.md` before step-2;
  Manual only for obvious ≤2-module / ≤2-consumer yellows; all red → Deep CDP;
  lead ≠ split planner (¬ in-seat Grok/Composer deep plans); thin apply only;
  mid-flight redirect → keep applied work, CDP only remaining unapplied (¬ revert to re-plan)
- Honor **three-tier stop model** — green autonomous; yellow concern; red rare (strong-reasoning pass before operator)
- ¬ invoke Stargate `doc-generate` on the **gradual** path — step 9 = CDP Sonnet 5
  (`team_dispatch(model=cdp/sonnet-5)`); Stargate `doc-generate` is **frontier-only** after explicit
  operator cost approval (paid Sonnet+Gemini API)
- ¬ invoke Stargate `docstring-enhance` on the **gradual** path — §5.6 /
  `/docstring-enhance` = CDP Sonnet; frontier API only with cost approval
- **Fail closed before step 9:** `docstring-quality` criticals must be 0; thicken
  warnings via §5.6 CDP when they would underfeed arch-doc projection
- ¬ invoke other Stargate pipelines (`modularize plan`, `code-review`) without
  tier-appropriate pause
- CDP `team_dispatch(model=cdp/…)` for deep splits, step-4 review, **Fable opportunity legs**,
  **step-9 Sonnet draft**, and step-11 arch-doc: **auto** after arc checkpoint /
  red gate — ¬ per-dispatch operator ask (see Approval vs transport + § Fable
  opportunity scanning). Structural Fable binds still need operator ratify before apply.
- Deep modularize CDP: **inline** architecture-invariants + modularize-discipline
  (+ ulg-architecture excerpt for ULG targets) into sealed `<invariants>` —
  ¬ slash those (not Claude slugs); ¬ URI-cite-only; ¬ retired `cortex://agent-skills/`
- ¬ start the overhaul before verifying Stargate is running when a **frontier**
  pipeline step is approved (`code-review` or frontier `doc-generate`)
- CDP legs (steps 4 deep, 9 draft, 11): cortex stage + `team_dispatch(model=cdp/…)` (+ optional
  ≤25-line bus pointer) — ¬ inline packet content in thread posts; ¬ default to
  `team_dispatch` handoff + manual push (a25444). **Escape:** `project_ask` when
  team_dispatch CDP unavailable
- ¬ modify `scripts/modularize` — it works as-is, this command calls it
- ¬ apply suggestion-level findings without explicit user instruction
- ¬ skip the docstring pass — it directly improves RAG retrieval quality
- ¬ skip docstring quality verification at step 5.5 — thin docstrings produce thin architecture docs
- ¬ skip the re-scan — final verification ensures no regressions
- Step 11 gradual default: `/review-arch-doc` (**CDP default**); `team-generate` only under
  `/overhaul frontier`; do not use `scripts/consult-frontier` for generated architecture docs
- Process one directory at a time — do not batch multiple directories
- Prefer event signals over high-noise request-path logs during refactors

Per-directory code overhaul: modularize, review, fix, docstring, verify.
Orchestrates the full quality pass for a single directory.

## Usage

```
/overhaul {directory}
/overhaul frontier {directory}
```

Where `{directory}` is a path relative to the project root (e.g.,
`services/universal-stargate/systems/proxy/`). When the first argument is
`frontier`, use the automated posture (see Operating posture table); otherwise
use gradual (default).

If you need to unblock a single oversized file first, use:

```
/overhaul-file {file}
```

Then return to `/overhaul {directory}` for the full subsystem pass.

Optional automated path (requires working `team-generate` / Stargate frontier dispatch):

```
/overhaul frontier {directory}
```

Use `frontier` only when frontier dispatch is verified end-to-end. Otherwise stay
on the default gradual posture below.

## Operating posture (default: gradual · autonomous supervised)

The default `/overhaul` run is **autonomous supervised** and **CDP-native** (Jupiter
`team_dispatch(model=cdp/…)` per Use the `claude-ai-cdp-navigation` skill;
`project_ask` = escape only). Cursor orchestrates;
deep-tier reasoning goes to web-anthropic via CDP; Stargate pipelines run only when
the user explicitly approves each call.

**Autonomy bind (operator ratified 2026-07-19):** proceed through green-tier work
without operator stops. Pause only on **yellow** (agent concern) or **red** (high
stakes). Composes with `reasoning-posture` **and** `frontier-reasoning-discipline`:
pin scope at arc birth; declare detent at tier choice; then steelman / calibrate;
`thinking_off ⇏ waive` evidence gates.

**Credit-budget bind (operator ratified 2026-07-20 · agent-bus:5473 ·
`decision:cursor-cdp-credit-budget-policy`):** Cursor lead **orchestrates**;
judgment / split **plans** / reviews / docs burn **claude.ai CDP** (subscription),
not Cursor credits. Checklist SOT:
`cortex://notes/system/threads/5473-credit-budget-checklist.md`.

| Lane | Who | Work |
|---|---|---|
| Judgment / plans / reviews / docs | CDP Opus / Sonnet / Fable via `team_dispatch(model=cdp/…)` | Deep + non-trivial yellow split **plans**, step-4 deep review, Fable legs, step-9/11 arch-doc |
| Execution / gates | Cursor lead (thin) | Birth CHECKPOINT, scan/vulture, thin **apply** of CDP plans, ruff/compileall/`check-imports`, deploy, commit |
| Cap | Manual in-seat | Only obvious yellow ≤2-module **and** ≤2 consumers — else CDP |
| Anti-pattern | ¬ | Lead model (Grok/Sonnet/Opus/Composer/`cursor-sdk`) authoring deep/red split **plans** in-seat; treating Composer apply as “outsourced”; **reverting already-applied package-shadow splits to re-send the same files to CDP** after a mid-arc credit-budget redirect |

**Mid-flight redirect (binding):** If the operator (or a sibling seat) corrects transport mid-arc — CDP vs Manual / credit-budget remind — **keep all applied work**. CDP **only** files that are still red/yellow **and** not yet applied (or whose apply failed audit). **¬** `git checkout` / delete package-shadow / re-plan files whose split already landed. Abort in-flight CDP only when it duplicates an already-applied file; do not abort healthy harvests for remaining work.

**Birth CHECKPOINT (mandatory before step-2 split work):** post the arc birth
CHECKPOINT citing the credit-budget checklist (copy block from that URI). Then
scan. Do not start Manual/Bulk/Deep planning until the cite is on the thread.

| Concern | Gradual (default) | Automated (`frontier` mode) |
|---|---|---|
| Deep file splits | CDP `team_dispatch(model=cdp/opus-5)` modularize packet (Opus; Cursor applies) | `/modularize` (team-generate E2E) |
| Code review | CDP `team_dispatch(model=cdp/opus-5)` review packet (Opus extract/correctness) | `/consult-review` (`code-review` pipeline) |
| Architectural strengthen | CDP `team_dispatch(model=cdp/fable)` **Fable 5** when opportunity scan fires (see below) | same CDP Fable (not Stargate) |
| Architecture doc review | `/review-arch-doc` (CDP default) | `/review-arch-doc team-generate` |
| Bulk split plans | `scripts/modularize plan` — one file at a time; green when plan audits clean | same, batched when user directs |
| Doc generation | step 9 — **CDP Sonnet 5** draft (`team_dispatch(model=cdp/sonnet-5)`; red gate) — **¬** Stargate `doc-generate` | Stargate `doc-generate` (paid API — operator must approve cost) |
| Docstring enhance (§5.6) | CDP Sonnet `team_dispatch(model=cdp/sonnet-5)` (`/docstring-enhance`) — **¬** Stargate API | `/docstring-enhance frontier` (paid API — operator must approve cost) |

### Fable opportunity scanning (standing — operator 2026-07-19)

Along the gradual arc, the lead **looks for chances to strengthen the subsystem
architecturally**, not only to finish hygiene steps. Prefer **Fable 5 via CDP**
(`team_dispatch(model=cdp/fable)`; ¬ API `anthropic/claude-fable-*`;
¬ `cursor/*` Fable) when an opportunity fires.

**Fire Fable (liberally when any match)** after logging a one-line opportunity in
the arc CHECKPOINT:

| Signal | Typical seams |
|---|---|
| Fragile / many-iteration history named in charter or operator prose | control-plane boundaries, admission vs feasibility, queue/backpressure |
| Step-4 Critical/Warning cluster on the same invariant family | eviction busy-matrix, hysteresis, CapacityPool lifecycle |
| Near-ceiling modules that absorb cross-cutting policy | extract vs redesign fork (Fable ranks; ¬ auto-split) |
| G5 `gap` or silent branch without reason fields | event/reason architecture, not log spam |
| Red-tier fork needing ranked binds + falsifiers | Fable = strong-reasoning pass before operator ratify |
| Post-split “correct but brittle” smell | L0∧L1∧L2 path-sim style refine; cascade Fable explores → lesser implements |

**Does not replace:** Opus step-4 extract review · Sonnet step-9 doc projection ·
green mechanical gates. **Structural apply** after Fable harvest remains **red**
(operator ratifies ranked bind). Multi-leg converse + Cowork multitask OK when
Fable fans recon. Log each Fable leg’s `execution_id` + `archive_uri` on the arc thread.

### Three-tier stop model (replaces uniform checkpoint gates)

| Tier | When | Who decides next |
|---|---|---|
| **Green — autonomous** | Scan/vulture (confirmed dead code only), CDP dispatch, apply **audit-pass** split, ruff/compileall/rescan, docstring pass, re-scan | Lead proceeds; log evidence in CHECKPOINT |
| **Yellow — agent concern** | Audit fail, PHANTOM symbols, scope surprise, review Critical, G5 `gap`, import-check fail, ambiguous split boundary | Lead posts **concern block** (fork + evidence + recommended bind); may proceed after self-verify or one-line operator ack |
| **Red — high stakes (rare)** | Event `signal=` renames, cross-subsystem coupling, first live-service deploy touch, step-9 arch-doc draft, Stargate `doc-generate` (frontier only), commit, destructive vulture deletes, admission/coordination paths, **Fable structural architecture binds** | **Strong reasoning pass first** (CDP **Fable 5** for architecture forks; CDP Opus for extract/review Criticals; or in-seat Opus-class bind) → operator ratification only after that pass surfaces the fork |

**Red is rare.** Default: classify as yellow and resolve in-seat. Escalate to red only
when the fork is irreversible, cross-subsystem, or deploy-touching. Red MUST NOT
surface to the operator as a raw pause — run a strong-reasoning review (CDP
`team_dispatch(model=cdp/fable|cdp/opus-5)` or equivalent) that returns a ranked bind + falsifiers; operator
sees that synthesis, not an undigested gate.

**Yellow concern block** (copy into CHECKPOINT or agent-bus sidecar):

```
CONCERN
tier: yellow
fork: <what decision is blocked>
evidence: <file:line | probe | audit item>
recommended: <proceed | escalate-red | rework>
note: <≤25 words>
```

**Mapping old steps → tiers:**

| Step block | Default tier |
|---|---|
| 1–1.5 scan + vulture | Green (vulture deletes: green only when confidence ≥80 and not dispatch/handler) |
| 2 plan + audit | Green when audit PASS; Yellow on fail |
| 3 apply split | Green when plan audit PASS |
| 4 review + triage | Yellow on Critical; Green on clean; G5 probe before trusting claims |
| 5–8 docstring/gates/rescan | Green |
| 9 arch-doc draft (CDP Sonnet 5) | Red |
| 10–11 arch doc review | Yellow on Critical; Red before replacing `docs/architecture/*.md` |
| 12 commit | Red |

**CHECKPOINT cadence:** post at wave boundaries and after any yellow/red resolution —
not after every green step. Include tier log (what ran autonomous vs what paused).

**CDP dispatch invariant**: substantive content lives in sealed prompt files
(`tmp/modularize-plans/`, `tmp/reviews/`) and preferably cortex-staged corpus
(`cortex://notes/system/threads/{arc-slug}/source/`) for fewer tool calls.
Invoke via `team_dispatch(model=cdp/…)` (`sidecar_ref` / `packet_path` after
staging); stage per Use the
`claude-ai-cdp-navigation` skill (`workspaces://` is readable — package hot paths
anyway). **Escape only:** bare `project_ask` when `team_dispatch` CDP unavailable
or `purpose=` inject required. Legacy `agent_bus` pointer posts remain valid when MCP is unavailable.

**Approval vs transport** (operator bind 2026-07-19; autonomy ratified 2026-07-19):

| Action | Operator confirm required? |
|---|---|
| Green-tier steps (see three-tier model) | **No** — proceed autonomous; log in CHECKPOINT |
| Yellow-tier concern | **Optional** — one-line ack or override; lead may self-resolve when evidence clears |
| Red-tier fork | **Yes** — only after strong-reasoning pass (CDP Fable for architecture; CDP Opus for extract Criticals; or in-seat Opus-class bind) |
| **CDP dispatch** (`team_dispatch(model=cdp/…)` for deep split plan, step-4 review, **Fable opportunity legs**, **step-9 Sonnet draft**, step-11 arch-doc) | **No** — once arc is live + red gate cleared for step 9, orchestrator fires CDP without per-dispatch ask; Fable legs auto when opportunity scan fires (operator may pre-authorize liberally per arc) |
| Stargate pipeline calls (`modularize plan`, `code-review`) | **Yes** — each invocation |
| Stargate `doc-generate` | **Gradual: banned.** **Frontier only** — each invocation (paid Sonnet+Gemini API ≈ $1–2/run; operator must approve cost) |
| Step 3 split apply | **No** when plan audit PASS (green); yellow/red per tier table |

CDP is default overhaul transport; it is not an optional side-path gated separately from the arc.

## Instructions

Execute these steps in order. Each step must complete before moving to the next.
Honor the **three-tier stop model** above. Green-tier: proceed without operator pause.
Yellow: emit concern block. Red: strong-reasoning pass, then operator ratification.
Step 4 manages its own triage flow per the chosen review path.

### 1. Scan for SLOC violations

```bash
source ~/.venvs/universal/bin/activate
scripts/modularize scan {directory}
```

Note any red (>400) or yellow (301-400) files.

### 1.5. Scan for cross-file dead code

```bash
vulture {directory} vulture_whitelist.py --min-confidence 80
```

Review findings. Delete genuinely dead code (unused functions, unreachable
branches, dead exports) before proceeding — removing dead code first means
the pipelines analyze cleaner files and don't waste tokens on code about
to be deleted.

Known false positives: `getattr()` dispatch, FastAPI route handlers, event
handler callbacks, `__init__.py` re-exports. Add confirmed false positives
to `vulture_whitelist.py`.

### 2. Generate split plans for oversized files

For each file flagged red or yellow in step 1, choose a tier. In gradual mode,
process **one file at a time**. **¬** ask before CDP `team_dispatch(model=cdp/…)` (credit-budget
bind — CDP is default transport). **Do** ask before each Stargate Bulk
`modularize plan` invocation.

| Tier | Tool | When to use |
|---|---|---|
| **Manual** (narrow exception) | Cursor lead thin split (read + apply) | Yellow (301–400) **only** when simple structure **and** ≤2 consumers **and** ≤2-module package-shadow — else escalate to Deep CDP. **¬** Manual for red (>400). **¬** in-seat lead inventing deep plans (Grok/Composer/`cursor-sdk` as planner). |
| **Bulk** | `scripts/modularize plan {file}` (Stargate `modularize` pipeline) | File ≤600 SLOC, simple consumer graph — **user approves each `plan` invocation** |
| **Deep** (gradual default) | CDP `team_dispatch(model=cdp/opus-5)` modularize (see §2.1) | Default for **all red**; yellow that fails Manual caps; Bulk coverage warnings / PHANTOM / complex consumers; file >600 SLOC |
| **Deep** (`frontier` mode only) | `/modularize {file}` (team-generate E2E) | Same triggers as deep CDP — use only under `/overhaul frontier` |

The bulk tier runs the modularize pipeline (analyze → critique → finalize) via
Stargate (`POST /v1/chat/completions`), model `modularize`. Cheap but lacks live
consumer reads. **Do not batch** bulk plans across files in gradual mode unless
the user explicitly requests it.

The deep tier (gradual) submits a six-block packet via CDP `team_dispatch(model=cdp/opus-5)` (§2.1;
Opus). CDP plans; **Cursor thin-applies** the harvested plan (no frontier Phase 2
auto-execution). Lead model identity (Grok, Sonnet, …) does **not** change this —
the lead orchestrates and applies; it does not replace CDP as the split planner.

If unsure in gradual mode: **Deep CDP** for red and for any yellow that is not an
obvious ≤2-module / ≤2-consumer Manual. Bulk only when operator approves each
`plan` call. **¬** default to Manual “because the lead is Grok / in Cursor.”

**Anchoring caveat — ¬ scaffold→densify here.** The packet-scaffolding pattern
(cheap tier drafts a scaffold, reasoner densifies — see
`handoff-packet-authoring.md` § Preliminary scaffold → densification) does NOT
apply to split planning. A preliminary "split skeleton" embeds **module boundary
design judgment**; a cheap draft can anchor the deep-tier reasoner into elaborating
a flawed decomposition instead of re-deriving boundaries from the live consumer
graph. Keep the tiers **either/or with escalation** (bulk *or* deep web-claude,
escalate on coverage warnings / PHANTOM symbols), ¬ chain a Composer-drafted plan
into a web-claude densification pass. Composer scaffolding is reserved for the
*review/doc-review packets* (steps 4, 11), not the split plan itself.

### 2.1. Deep tier — CDP modularize handoff (gradual default)

When bulk is insufficient or skipped, build and submit a modularize packet via
**`team_dispatch(model=cdp/opus-5)`** (Use the `claude-ai-cdp-navigation` skill;
`project_ask` = escape only) instead of
calling `/modularize` or waiting on manual operator push:

1. Gather artifacts per `/modularize` §2.1–2.2 (source, consumers via grep, composed `<invariants>`, `<architecture>` replacement table for violations in this file).
2. **Skill delivery (BINDING — claude.ai):** modularize plans need architecture
   context **inlined into the sealed prompt**. These are **not** Claude
   Customize Skills slugs (`architecture-invariants` + `modularize-discipline`
   are life_local; `ulg-architecture` is cursor_only) — **¬ slash-inject them**
   (silent no-op).

   **Inline into `<invariants>` (required):**
   | Content | Why |
   |---|---|
   | `architecture-invariants` | Universal floor — quality/SLOC, transport, events, no-bc |
   | `modularize-discipline` | Split rules — forbidden names, package-shadow, consumer surface |
   | `ulg-architecture` `[ulg:*]` floor | When target is under `services/` / `libs/` in this repo |

   Paste short excerpts (tag index + rules that bind this file). Optional cortex
   stage of full skills under `cortex://notes/system/threads/{arc-slug}/skills/`
   is backup only — **does not** replace inline. **¬** cite retired
   `cortex://agent-skills/*.md`. **Fail closed** if the sealed prompt lacks
   inlined architecture-invariants + modularize-discipline before submit
   (`ulg-architecture` excerpt required for ULG `services/` / `libs/`).

   Optional shared_sync chip helpers (real Claude slugs, when on chip):
   `/frontier-reasoning-discipline`, `/evidence-review-discipline`,
   `/no-silent-inference` — additive only; ¬ substitutes for the three inlines.
3. Stage corpus under `cortex://notes/system/threads/{arc-slug}/source/` (source file, consumer manifest). See Use the `claude-ai-cdp-navigation` skill § web-anthropic-cdp dispatch constraints.
4. Write `tmp/modularize-plans/{sanitized-name}-packet.md` — six-block format from `architecture-handoff-protocol.mdc` (same block table as `/modularize` §2.3). Required skill **inlines** live inside `<invariants>` (fleet rule — Use the `claude-ai-cdp-navigation` skill).
5. Submit via `team_dispatch(op=generate, model=cdp/opus-5, contract=light-bounded, packet_path=tmp/modularize-plans/…, dispatch_thread_id=<arc-thread>)` — wait via `poll_hint` / `agent_bus.wait` until `archive_uri` present. **Escape only:** `project_ask(op=submit, …)` then poll via `project_ask(op=poll, execution_id=<id>)`. **NEVER curl localhost :8765** (web-fetcher) for `/v1/project-ask/*` — MCP poll only on escape path (cdp-ask is :8770 via `PROJECT_ASK_URL`).
6. **Wait for harvest (bounded).** After CDP submit, wait via `poll_hint` — or MCP `project_ask(op=poll, …)` on escape path only — not curl/REST — until `archive_uri` is present **or** the **lead wait budget** elapses, whichever comes first.
   - **Lead wait budget:** wall-clock **420 seconds** from submit (`N` — **provisional-v0**). This is **separate from** satellite `timeout_s` (idle semantics pause during active Opus — see Use the `claude-ai-cdp-navigation` skill § Idle vs in-flight).
   - **Dogfood calibration:** record wall-time-to-`archive_uri` on deep-tier dispatches; if median healthy harvest over **≥5** dispatches exceeds **N**, raise **N** (or promote deferred SLOC-tiering per Q11).
   - Today's poll surface does not expose tool progress — treat `status=running` as inconclusive until harvest or budget expiry.
   - **Do not re-submit** the same modularize packet unprompted (24911 anti-redispatch applies to any recovery path).

7. **On harvest (`archive_uri` present):** Audit the returned plan per `/modularize` §2.6 and `modularize-discipline`. **Green** when audit PASS → proceed to step 3; **yellow** on fail (concern block). Plans harvested without architecture-invariants + modularize-discipline **inlined** in the sealed prompt start **yellow** — re-audit forbidden names and SRP before apply; ¬ treat as automatic green.

8. **On lead wait budget expiry without `archive_uri`:** Execute **in-seat fallback** (do not treat as overhaul failure):
   a. **Plan:** Lead reads source + consumers; produce a **line-range package-shadow split plan** per `/modularize` apply path (reference `/modularize` §2.6 and `modularize-discipline` — do not restate layout rules inline).
   b. **Minimal audit:** Before apply, verify the §2.6 / `modularize-discipline` checklist (forbidden module names, package-shadow layout, public import surface preserved for consumers grep-found in step 1, cross-module import violations, logger anti-patterns, proposed modules ≤300 SLOC target via scan estimate).
      - **Runtime gate (green path):** run `scripts/check-imports` on affected packages (or unit tests touching the split module) — must pass before **green** apply; failure → **yellow CONCERN**, never silent green.
      - **Pass** static + runtime → **green** apply with CHECKPOINT note (below). **Any static fail, runtime fail, or ambiguous boundary** → **yellow CONCERN** block before apply.
   c. **Re-poll before abort:** Immediately before abort, **re-poll `archive_uri` once**. If present → cancel fallback; audit CDP plan per step 7; proceed on tier. If still absent → apply in-seat plan per tier rule from (b), **then** abort hygiene:
      - `project_ask(op=abort, execution_id=…)` on the in-flight dispatch (**escape path only**).
      - Poll until `status=aborted` confirmed.
      - **Do not re-submit** the same packet; **do not** new `--register` to "replace" the execution (Use the `claude-ai-cdp-navigation` skill § Anti-redispatch).
   d. **CHECKPOINT note (required on expiry path):**
      ```
      cdp_wait_budget_expired
      execution_id: <id>
      registration_id: <id if known>
      elapsed_wall_s: >=420
      lead_wait_budget: provisional-v0 (N=420)
      fallback: in-seat line-range split
      harvested_after_abort: yes|no
      re_poll_archive_uri: absent|present
      tier: green|yellow (per audit)
      ```
   e. Proceed to step 3 apply when audit tier permits.

For execution-phase iteration, submit a follow-up `team_dispatch(model=cdp/opus-5, …)` with `<prior_pass>` context
rather than opening a redundant parallel dispatch.

Under `/overhaul frontier`, replace steps 1–8 above with `/modularize {file}`.

### 2.5. Pipeline model configuration

The bulk pipeline's `plan_model` and `execute_model` live in
`pipelines/modularize/models.yaml`. The current values are tuned defaults; do
not auto-update them as part of overhaul. Update them through a focused
consultation cycle on the `pipelines/modularize` subsystem when a clearly
stronger candidate is empirically demonstrated on this codebase, not from a
public leaderboard.

The deep tier under `/overhaul frontier` (`/modularize`) hardcodes `openai/gpt-5.5` per
`projects/.cursor/rules/handoff-dispatchers.mdc` and is not affected by `pipelines/modularize/models.yaml`.
The web-claude deep tier is not affected by `models.yaml` either.

If a service restart is required after editing pipeline model configs, ask the
user to run `./manage` — do not start/stop services directly.

Present each plan to the user for review.

For each oversized file plan, also show a proposed package-shadow directory
tree before requesting step 3 approval.

Example format:

```text
path/to/target/
  __init__.py
  module_a.py
  module_b.py
```

Requirements:

- Show one tree per planned split target file.
- Include all planned submodules and `__init__.py` re-export surface.
- Mark uncertain symbol placement explicitly; resolve with user before edits.

### 3. Apply split plans

For each approved split plan, implement the module extraction:

- Default to **package-shadow** layout for file splits:
  - `path/to/target.py` -> `path/to/target/` package directory
  - split modules live under `path/to/target/*.py`
  - `path/to/target/__init__.py` re-exports preserve the public import surface
  - remove original `path/to/target.py` after package exports are in place
- Create the new module files as specified
- Move definitions to their target modules
- Update imports across all consumers
- Create `__init__.py` with re-exports if needed

If a generated plan has coverage warnings, PHANTOM symbols, or is otherwise
underspecified, **escalate to the deep tier** instead of asking the user to
approve a partial bulk plan:

- **Gradual (default)**: web-claude handoff per §2.1
- **`frontier` mode**: `/modularize {file}`

Do not approve and apply a partial bulk plan when deep escalation is indicated.

For plans that look complete and consistent, proceed with apply under **green** tier
(audit PASS). **Yellow** when audit fails or scope is ambiguous.

**Dispatch (default, when applying from a non-Cursor seat):** `team_dispatch(op=generate, seat=cursor-sdk, packet_path=tmp/reviews/{unit}-implement-packet.md, contract=implement, dispatch_thread_id={arc-id})` — auto Composer, no IDE pickup. The split-apply packet MUST be **dense** (every move/re-export/consumer-update pinned; Composer executes mechanically). Use `cursor-implement` handoff only when the SDK worker is unavailable or operator-attended IDE execution is explicitly wanted. Full policy: `docs/agent-guides/skills/consult-routing.md` § Implement lane (cortex SOT: `agent-skills/consult-routing.md`).

### 4. Code review (choose path)

**Gradual (default) — tiered review transport**

Scope files in `{directory}`:

```bash
git diff --name-only -- {directory}
find {directory} -name '*.py' -not -path '*/__pycache__/*' | sort
```

**Event coverage + G5 (binding — before triage):** Run an `EVENTS-PROBE` pass per
`path-sim` § Events/gap probe (grammar SOT:
`cortex://notes/system/threads/ulg-path-sim-events-g5-densify.md`). Map review
findings to expected signal families using
`cortex://notes/system/threads/ulg-path-sim-events-g5-v1-implement-densify.md`
(overhaul step-4 event map). §4.5 covers observability noise policy; G5 covers
evidence verification — do not fork grammar into this command. `/overhaul` does
**not** invoke `/path-sim`; it **references** the same probe block at step 4 closeout.

| Tier | Trigger | Transport |
|---|---|---|
| **Deep / cross-subsystem** | Multiple changed packages, external callers, or yellow/red scope per three-tier model | CDP `team_dispatch(model=cdp/opus-5)` bus-nudge — **answer-3 preflight (reject-incomplete):** stage cortex corpus under `cortex://notes/system/threads/{arc-slug}/…` with **every external caller** of each changed public symbol + omission-disclosure for unstaged paths the verdict would need; build six-block packet to `tmp/reviews/overhaul-{subsystem}-cdp-review-packet.md`; **required** ≤25-line bus pointer on arc coordination thread (URI table only); `team_dispatch(op=generate, model=cdp/opus-5, sidecar_ref=cortex://…, dispatch_thread_id=…)`, wait via `poll_hint` until `archive_uri`; **escape:** `project_ask` submit+poll when CDP team_dispatch unavailable; **do not** use `team_dispatch` handoff + manual operator push (friction a25444) |
| **Narrow / single-subsystem** | Single package, ≤2 consumers, green tier | In-seat Grok High or `team_dispatch(op=generate, seat=cursor-sdk, contract=light-bounded)` on staged diff — no open web thread, no push reminder |

**Deep tier packet** — six-block; skill delivery per Use the `claude-ai-cdp-navigation`
skill § Skill delivery — fleet rule:

- **Inline** into `<invariants>` (not Claude slugs): `architecture-invariants` +
  `ulg-architecture` tag floors relevant to changed code. **¬ slash.**
- **Claude-slug engage** at top of sealed prompt (`/` line or `Use the … skill`):
  `evidence-review-discipline`, `frontier-reasoning-discipline`,
  `no-silent-inference` (on Customize staging).
- Also: `<scope>`, `<corpus>` (changed manifest + staged excerpts),
  `<task_guidance>` (post-overhaul split review — correctness, invariants, event
  gaps, docstring quality, §4.5 noise), `<output_format>` (severity-grouped
  findings with `Evidence:`). Prefer cortex-packaged corpus per CDP skill
  (speed/targeting; `workspaces://` readable when exploration is named).
  **Fail closed** if required inlines missing before submit.

**Lead sequence (deep tier, binding):** answer-3 preflight PASS → stage → **required** thin bus pointer on arc thread →
`team_dispatch(model=cdp/opus-5)` submit **before** turn close → suppress push reminder (24628) → wait
harvest → triage into Applied / Pending / Rejected / Suggestions.

Triage findings: apply Critical only after validation; yellow concern block on
ambiguous scope. Alternatively, `/diff-review` when manifest workflow fits better.

**CDP-down (deep tier):** halt with `BLOCKED:applied-unreviewable` if code already
applied; report on arc thread — ¬ silent fallback to `team_dispatch` handoff.
Legacy web-claude handoff only when operator explicitly chooses fallback after CDP-down report.

**`frontier` mode — pipeline review**

Execute the full `/consult-review` workflow on the directory's Python files.
This handles pre-flight pipeline availability checks, SLOC gates, batching,
invariant validation, event coverage gap detection, and the closing checklist.

Use the same scope commands as above, then follow `/consult-review` instructions.

The closing checklist (Applied / Pending / Rejected / Suggestions) replaces
manual finding triage. All fixes require user approval — do not auto-apply
Warning or Suggestion items.

Event coverage gaps surfaced by the review pipeline are handled within
`/consult-review` (see its Event Coverage Gaps → Suggestions section).
New signals follow `docs/event-contracts.md` conventions.

### 4.5. Observability-first noise reduction

Apply this policy across touched files during overhaul:

- Lean on structured events for request-path observability; avoid verbose
per-request/per-candidate logs at `info` level when event coverage exists.
- Demote repetitive branch diagnostics to `debug`; keep `info`/`warning` for
actionable boundaries and operator-relevant summaries.
- Preserve one concise summary log per major success/failure branch where
useful for quick local triage.
- If a noisy log is reduced or removed, ensure equivalent (or better) event
signal coverage remains.

When generating/applying review fixes, treat these as high-value event
opportunities before introducing new logs:

- Decision/branch outcome events with explicit reason fields
- Retryable vs non-retryable failure boundary events
- Queue/backpressure lifecycle events (enter/wake/timeout/cancel)
- Spillover/fallback/handoff transition events
- Invariant guard-block events (condition blocked action)
- Recovery/unblocked events after prior failure states

### 5. Docstring pass

Use the `docstring-quality` skill (canonical slug — seat self-fetches).
Ensure every module, class, and public function meets that bar before step 5.5.
Step 9 projects docstrings into the architecture doc — thin docstrings produce
thin architecture docs.

#### Quality standard

**Module docstrings** (≥15 words): What the module does, who calls it, key
invariants or design decisions.

Good:

```
"""Request routing and gateway selection for federated inference.

Implements the core routing algorithm that selects which gateway should
handle an incoming request. Called by the proxy layer after model ID
resolution. Routing decisions are based on model availability, capacity
constraints, and latency preferences.

Invariant: never routes to a gateway that lacks the requested model or
has exhausted its capacity budget.
"""
```

Bad:

```
"""Request routing module."""
```

```
"""Handles routing."""
```

**Class docstrings** (≥15 words): Purpose, lifecycle (how/when created
and destroyed), key methods worth knowing about.

Good:

```
"""Tracks in-flight requests and enforces per-gateway capacity limits.

Created once per Stargate instance at startup. Maintains a concurrent
map of active requests keyed by (gateway_id, model_id). Capacity is
released when the request completes or times out.

Key methods:
    acquire(): Reserve a capacity slot, blocking if at limit.
    release(): Free a capacity slot after request completion.
    snapshot(): Return current utilization for telemetry.
"""
```

Bad:

```
"""Capacity tracker."""
```

```
"""Class for tracking capacity."""
```

**Function docstrings** (≥10 words): What the function does (not just
restating the name), parameter semantics when non-obvious, return value,
side effects (event emissions, state mutations, I/O).

Good:

```
def resolve_model_id(raw_id: str, aliases: dict[str, str]) -> ModelId:
    """Normalize and resolve a raw model identifier.

    Handles alias expansion, version suffix stripping, and quantization
    tag normalization. If raw_id matches an alias key, the alias target
    is used before normalization.

    Returns a validated ModelId or raises ModelIdError if the format
    is unrecognizable after all normalization attempts.
    """
```

Bad:

```
def resolve_model_id(raw_id, aliases):
    """Resolve model ID."""
```

```
def resolve_model_id(raw_id, aliases):
    """Resolves the model id from raw id and aliases."""
```

#### Audiences

Written for three consumers:

1. **Humans** reading code — explain the "why", not just the "what"
2. **Agents** navigating the codebase — name the callers, invariants,
  and relationships so an LLM can reason about dependencies
3. **Embedding models** chunking for RAG — use distinctive terms that
  differentiate this module/class/function from similar ones

#### Scope

Skip private helpers (`_name`) unless their logic is non-obvious.
For `__init__.py` files, a brief re-export summary is sufficient.

### 5.5. Verify docstring quality

Run the docstring quality checker:

```bash
source ~/.venvs/universal/bin/activate
scripts/docstring-quality scan {directory}
```

This checks every module, public class, and public function for:

- **empty** (critical): No docstring at all
- **too_short** (warning): Below word count threshold for scope
- **name_echo** (warning): First sentence just restates the element name

If there are critical issues (exit code 1), fix them and re-run before
proceeding. For warnings, review the report and improve any docstrings
that would produce thin architecture doc sections.

The goal: every docstring should give step 9 enough material to
write a substantive architecture doc paragraph, not just a label.

### 5.6. Docstring enhancement pass (gradual default — CDP Sonnet 5)

When local/manual cleanup still leaves thin content (**warnings** that would
starve step-9 arch-doc projection), run CDP Sonnet enhance — **not** the
Stargate API pipeline:

```
/docstring-enhance {directory}
```

Use the `claude-ai-cdp-navigation` skill. Template:
`cortex://notes/system/templates/cdp-overhaul-docstring-enhance.md`.
Apply harvest via `scripts/docstring-apply`; re-run step 5.5 until criticals
are 0 and feedstock is thick enough for step 9.

**Forbidden on gradual:** `/docstring-enhance frontier` / curl `model=docstring-enhance`
(paid Stargate API). Frontier override only with explicit operator cost approval.

Use when:

- warnings remain concentrated on module/class/function quality (not missing files)
- prior arch drafts produced weak sections or repeated HUMAN markers
- credit-budget bind: burn Claude **subscription**, not API credits

After apply + step 5.5 green on criticals, proceed to quality gates / step 9.

### 6. Quality gates

```bash
source ~/.venvs/universal/bin/activate
ruff check --select=UP --fix {directory}
ruff format {directory}
python -m compileall -q {directory}
ruff check {directory}
```

**Import resolution (mandatory).** `compileall` checks syntax only — it does
not execute imports. After any split, package-shadow move, or relative-import
change under `services/universal-stargate/`, run:

```bash
scripts/check-imports --stargate-entry {directory}
```

For `libs/` changes:

```bash
scripts/check-imports libs/{package}/
```

When `{directory}` touches Stargate source, also verify the service entry
point loads (included automatically by `--stargate-entry`):

```bash
# imports systems.proxy.app — same path as start_proxy.py
```

`quality_gate(files=[...])` runs the same import check when MCP is available.
**Invariant:** compileall pass ≠ imports pass. Do not commit or call
`sync_restart stargate` until `check-imports` exits 0.

For Stargate-touching changes, run post-apply (after step 3 or step 6):

```bash
manage(action="sync_restart", service="stargate")
manage(action="wait_healthy", service="stargate", timeout=120)
```

Per `service-lifecycle` skill — source under `services/universal-stargate/`
requires deploy verification, not just static gates.

### 7. Unused imports

```bash
ruff check --select F401 {directory}
```

Fix any remaining unused imports.

### 8. Re-scan

```bash
scripts/modularize scan {directory}
```

Verify all files are green (≤300 SLOC).

For any file still yellow/red after the bulk pass, **escalate to the deep tier
per file**:

- **Gradual (default)**: web-claude handoff per §2.1
- **`frontier` mode**: `/modularize {still-red-file}`

Do not loop the bulk pipeline a second time on the same file — if it didn't
land cleanly the first time, escalate to web-claude (or `/modularize` under
`frontier` mode). Re-run step 8 after each deep split completes; continue until
all files are green or the user explicitly defers a remaining violator.

### 9. Generate/update architecture doc (gradual default — CDP Sonnet 5)

> **Scope of this arc (non-goal):** the draft re-projects what the source
> *declares* (docstrings, signatures, imports); it does not read function bodies
> and does not attest behavior. Bug discovery belongs to steps 1.5 (vulture),
> 4 (body-reading review), and 5 (writing honest docstrings). A "verified" arch
> doc is NOT a behavioral attestation.

**Docstring feedstock gate (BINDING — fail closed):** before step-9 submit:

```bash
scripts/docstring-quality scan {directory}
```

- **Criticals > 0** → halt; finish §5 / §5.6 (CDP Sonnet enhance) until criticals
  are 0. **¬** draft arch-doc from empty docstrings.
- **Warnings concentrated** (too_short / name_echo on public surface that step-9
  will project) → run §5.6 CDP enhance (or explicit operator waive with CHECKPOINT
  note). Skipping thicken underfeeds the managed arch doc and breaks the flow.

**Gradual gate (red):** summarize steps 1–8 outcomes (include docstring scan
summary) and ask the user before firing the CDP Sonnet draft. Skip this step
entirely if the user defers architecture doc work.

**Gradual transport (BINDING):** Jupiter CDP `team_dispatch(model=cdp/sonnet-5)`
(Claude.ai subscription). Prefer `team_dispatch(model=cdp/…)`; `project_ask` =
escape only (SOT: `consult-routing` § Surface gate). Use the
`claude-ai-cdp-navigation` skill.

**Forbidden on gradual:** Stargate `doc-generate` / `curl … model=doc-generate`
(paid `anthropic/*` + Gemini API — historically ~$1–2 and ~6 min per run).

**`frontier` mode only:** Stargate `doc-generate` remains an explicit paid
override. Before invoking it, warn the operator of API cost and get approval;
then use the curl recipe under §9b below.

#### 9a. Gradual — CDP Sonnet 5 draft

1. **Stage cortex corpus** under
   `cortex://notes/system/threads/{arc-slug}/source/` (prefer packaging for
   fewer tool calls; `workspaces://` is readable — name it when exploration
   is encouraged):
   - Module inventory from `scripts/modularize scan {directory}` →
     `…/source/doc-scan-summary.txt`
   - Key source files / docstring excerpts covering claimed areas →
     `…/source/` (or `doc-source-manifest.txt` listing staged URIs)
   - Existing arch doc if present →
     `…/source/docs/architecture/{subsystem}.md` (mirror)
   - **No paid extract call** — lead-built inventory only

2. **Seal packet** to `tmp/reviews/overhaul-{subsystem}-doc-draft-packet.md`
   from template `cortex://notes/system/templates/cdp-overhaul-doc-draft.md`
   (six-block shape; prefer cortex URIs for the hot path).

3. **Submit + wait** (prefer team_dispatch CDP — NEVER curl `:8765` for project-ask):

```
team_dispatch(
  op="generate",
  model="cdp/sonnet-5",
  contract="light-bounded",
  packet_path="tmp/reviews/overhaul-{subsystem}-doc-draft-packet.md",
  # or sidecar_ref="cortex://…" after staging the sealed packet
  dispatch_thread_id="<arc-thread>"
)
# → poll_hint; wait until archive_uri is set
```

**Escape only:** `project_ask(op=submit, …, model=sonnet-5)` →
`project_ask(op="poll", execution_id="<id>")`.

4. **Materialize draft artifacts** from harvest / archive:

```bash
SUBSYSTEM="$(basename "{directory%/}")"
DOC_PATH="docs/architecture/${SUBSYSTEM}.md"
# Write harvested architecture markdown (lead extracts from archive_uri body):
#   → ${DOC_PATH}.generated
# Shim for §10–11 /review-arch-doc (empty arrays OK if harvest has no inventory):
cat > /tmp/doc-generate-result.json <<'EOF'
{
  "unsupported_claims": [],
  "missing_coverage": [],
  "human_markers": [],
  "review_notes": ["cdp-sonnet-5 draft; not Stargate doc-generate"],
  "inventory_sha": "cdp-sonnet-draft"
}
EOF
```

If CDP fails or harvest is incomplete: **halt** — report to operator.
¬ fall back to Stargate `doc-generate` on the gradual path.

#### 9b. Frontier override — Stargate `doc-generate` (paid API)

Only under `/overhaul frontier` **and** after operator cost approval:

```bash
curl -s http://localhost:9999/v1/models | jq '.data[] | select(.id == "doc-generate")'
DIRECTORY_ABS="$(realpath "{directory}")"
DOC_GEN_RESPONSE="$(curl -s -X POST http://localhost:9999/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"doc-generate\",\"messages\":[{\"role\":\"user\",\"content\":\"${DIRECTORY_ABS}\"}]}")"
echo "$DOC_GEN_RESPONSE" > /tmp/doc-generate-response.json
DOC_JSON="$(echo "$DOC_GEN_RESPONSE" | jq -r '.choices[0].message.content // empty')"
echo "$DOC_JSON" | jq . > /tmp/doc-generate-result.json
SUBSYSTEM="$(basename "{directory%/}")"
DOC_PATH="docs/architecture/${SUBSYSTEM}.md"
echo "$DOC_JSON" | jq -r '.doc_markdown' > "${DOC_PATH}.generated"
```

If extraction/parsing fails: save `/tmp/doc-generate-response.json`, report,
do not continue to commit until step 9 succeeds.

### 10. Review generated doc

Review `${DOC_PATH}.generated` before replacing `${DOC_PATH}`:

1. Verify every factual claim traces to extracted docstrings/signatures /
   staged corpus.
2. Resolve all `unsupported_claims` and `missing_coverage` items from
  `/tmp/doc-generate-result.json` (may be empty for CDP Sonnet drafts).
3. Address each `<!-- HUMAN: ... -->` marker with authored content where needed.
4. Ensure generated sections remain wrapped by:
  - `<!-- GENERATED:START -->`
  - `<!-- GENERATED:END -->`
5. Preserve authored sections marked by `<!-- AUTHORED -->` where applicable.

When review passes:

```bash
mv "${DOC_PATH}.generated" "${DOC_PATH}"
```

### 11. Architecture doc review (gradual default — CDP)

Run `/review-arch-doc` on the generated architecture doc. **Gradual mode omits
the dispatcher argument** — `/review-arch-doc` defaults to **CDP-native**
`team_dispatch(model=cdp/opus-5)` (not legacy web-claude handoff + manual push).

```bash
/review-arch-doc "${DOC_PATH}" /tmp/doc-generate-result.json --source {directory}
```

Lead stages cortex corpus (**answer-3 preflight:** source coverage for **every area the doc asserts** + omission-disclosure; reject-incomplete), fires CDP bus-nudge with **required** ≤25-line bus pointer on arc coordination thread, polls harvest. Follow
`/review-arch-doc`'s validation contract before applying any finding:

1. Apply Critical findings that survive local rule validation.
2. Ask the user before applying Warning or Suggestion findings.
3. Reject findings that contradict the extraction inventory or workspace rules.
4. Record surfaced/deferred findings in the review artifact.

**`frontier` mode** — synchronous automated review when frontier dispatch is verified:

```bash
/review-arch-doc team-generate "${DOC_PATH}" /tmp/doc-generate-result.json --source {directory}
```

If `team-generate` fails, do not silently fall back. Offer the user:

- retry later (Stargate may recover)
- continue with CDP default (gradual)
- `local-self` (in-context review, no external dispatcher)
- legacy `web-claude` handoff only after explicit operator fallback when CDP unavailable

Proceed only with the user's chosen path.

### 12. Commit code + docs together

Stage both code and architecture doc updates in one commit:

```bash
git add {directory} "${DOC_PATH}"
git commit -m "overhaul: {directory} (code + architecture doc)"
```

Do not split code and doc updates into separate commits.

## Rules

- Default posture is **gradual / autonomous supervised**; use `/overhaul frontier` only
  when team-generate is verified end-to-end
- **Credit-budget (5473):** birth CHECKPOINT cites
  `cortex://notes/system/threads/5473-credit-budget-checklist.md` before step-2;
  Manual only for obvious ≤2-module / ≤2-consumer yellows; all red → Deep CDP;
  lead ≠ split planner (¬ in-seat Grok/Composer deep plans); thin apply only;
  mid-flight redirect → keep applied work, CDP only remaining unapplied (¬ revert to re-plan)
- Honor **three-tier stop model** — green autonomous; yellow concern; red rare (strong-reasoning pass before operator)
- ¬ invoke Stargate `doc-generate` on the **gradual** path — step 9 = CDP Sonnet 5
  (`team_dispatch(model=cdp/sonnet-5)`); Stargate `doc-generate` is **frontier-only** after explicit
  operator cost approval (paid Sonnet+Gemini API)
- ¬ invoke Stargate `docstring-enhance` on the **gradual** path — §5.6 /
  `/docstring-enhance` = CDP Sonnet; frontier API only with cost approval
- **Fail closed before step 9:** `docstring-quality` criticals must be 0; thicken
  warnings via §5.6 CDP when they would underfeed arch-doc projection
- ¬ invoke other Stargate pipelines (`modularize plan`, `code-review`) without
  tier-appropriate pause
- CDP `team_dispatch(model=cdp/…)` for deep splits, step-4 review, **Fable opportunity legs**,
  **step-9 Sonnet draft**, and step-11 arch-doc: **auto** after arc checkpoint /
  red gate — ¬ per-dispatch operator ask (see Approval vs transport + § Fable
  opportunity scanning). Structural Fable binds still need operator ratify before apply.
- Deep modularize CDP: **inline** architecture-invariants + modularize-discipline
  (+ ulg-architecture excerpt for ULG targets) into sealed `<invariants>` —
  ¬ slash those (not Claude slugs); ¬ URI-cite-only; ¬ retired `cortex://agent-skills/`
- ¬ start the overhaul before verifying Stargate is running when a **frontier**
  pipeline step is approved (`code-review` or frontier `doc-generate`)
- CDP legs (steps 4 deep, 9 draft, 11): cortex stage + `team_dispatch(model=cdp/…)` (+ optional
  ≤25-line bus pointer) — ¬ inline packet content in thread posts; ¬ default to
  `team_dispatch` handoff + manual push (a25444). **Escape:** `project_ask` when
  team_dispatch CDP unavailable
- ¬ modify `scripts/modularize` — it works as-is, this command calls it
- ¬ apply suggestion-level findings without explicit user instruction
- ¬ skip the docstring pass — it directly improves RAG retrieval quality
- ¬ skip docstring quality verification at step 5.5 — thin docstrings produce thin architecture docs
- ¬ skip the re-scan — final verification ensures no regressions
- Step 11 gradual default: `/review-arch-doc` (**CDP default**); `team-generate` only under
  `/overhaul frontier`; do not use `scripts/consult-frontier` for generated architecture docs
- Process one directory at a time — do not batch multiple directories
- Prefer event signals over high-noise request-path logs during refactors


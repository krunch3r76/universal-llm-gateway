Lean ladder — thin wrapper over the lean-dispatch paste template.

## Purpose

Invokes the lean-dispatch ladder for one work item: externalized confer, plan, optional plan review, implement, and pre-land review. A successful run drives Auto-runnable work through land → housekeeping (residuals filed via `residual-imprint`) → tab rename `{n} [closed] {slug}`. Procedure, leg order, wake modes, guards, review rosters, and completion rules are stated only in the template. Load the template, fill slots from the parameters below, and follow its sections in order.

**Template SOT (fill and run):** `cortex://notes/system/templates/lean-dispatch-ladder.md` — `fs md_list`, then `md_read` the sections you need; plain `fs(op="read")` returns only a manifest.

Use the `lean-dispatch` skill (skill=`lean-dispatch`; Cursor arc=this command). Use the `consult-routing` skill. Use the `retrieval-before-authoring` skill.

When the plan may assign **host-checkout diagnostics or named pytest** (not lane-B implement), follow template **Leg 2c — Grok host PREFLIGHT** and load `runbook:grok-host-preflight` (`cortex://notes/system/runbooks/grok-host-preflight.md`) before routing.

## Usage

```
/lean-ladder "<question>" [slug=<slug>] [planner_effort=<low|medium|high|xhigh>] [key=value ...]
```

**Standing planner (for now):** Glass tab on `cursor/claude-fable-5-2` with operator-chosen `planner_effort` (default `high`). Opt out with `planner=here` to keep the ladder on this tab.

## Parameters

| Param | Required | Default | Meaning | Template |
|---|---|---|---|---|
| `question` | yes | — | One line, operator's words | `{{question}}` → § Question (one line) |
| `slug` | no | **inferred** (see Slug inference) | `work_key=todo:<slug>` | `{{slug}}` throughout |
| `packet` | no | `cortex://notes/tmp/packets/<slug>-packet.md` | Seed sidecar URI | `{{packet}}` |
| `retrieval_report` | no | `cortex://notes/tmp/packets/<slug>-retrieval-report.md` | Seed retrieval report | `{{retrieval_report}}` |
| `architect_model` | no | `cdp/fable-5.2` (when CDP Fable open) | Leg 1 wire — **always** `entity_get` `decision:cdp-fable-channel-outage` before accept (`consult-routing` § CDP transport). Open until-window ⇒ do **not** fire `cdp/fable-*`. Resolve substitute from assertion attrs (`substitute_primary` / `substitute_chain`) or walk: (1) `cdp/opus-5-max` (2) `cursor/claude-fable-5-2` (Cursor pool — not blocked by CDP Fable outage) (3) `cdp/opus-5.5` high (4) nested-grok `cursor/grok-4.7` xhigh. Echo the resolved wire. Operator pin wins. | `{{architect_model}}` |
| `architect_effort` | no | `high` | Leg 1 `reasoning_effort` | `{{architect_effort}}` |
| `implement_model` | no | `cursor/composer-2.5` | Leg 3 wire (`cursor/*` only) | `{{implement_model}}` |
| `lane` | no | `B` | Leg 3 lane | `{{lane}}` |
| `density_triage` | no | `mechanical` | Todo attribute; Leg 3 flag | `{{density_triage}}` |
| `planner` | no | `glass:8` | `glass:<n>` (default) or `here` — selects § Wake registration subsection. Standing default spawns a Fable planner tab; pass `planner=here` to run legs on this tab instead | prose |
| `planner_model` | no | `cursor/claude-fable-5-2` | Glass spawn **explicit** `model=` pin (Other Models pool is `explicit_pin_only` — omit 422s). Wire form `cursor/claude-fable-5-2` (or bare catalog `claude-fable-5-2`); **hyphenated** only. Never dotted `claude-fable-5.2` (alias map resolves bare `fable-5.2` only; dotted form → `model_not_in_ui`). Never `cursor/grok-4.7` as planner. No `ban_override` (Fable is not in `model_bans`). Card knobs: `context` / `effort` / `fast` only — **no `thinking`** on 5-2. `$/M` → `config/model_rates.yaml` (do not inline). | `{{planner_model}}` |
| `planner_effort` | no | `high` | **Choose per invocation** — Glass spawn `effort=` (`low` / `medium` / `high` / `xhigh`). Echo the resolved value; pin on `spawn_tab` even when it matches the card default | `{{planner_effort}}` |
| `wake` | no | `subscribe` (**standing operator rule: always register**) | `subscribe` or `none` — `none` only when the operator types `wake=none` in this invocation; never inferred, never chosen to dodge a registration error — § No-wake resume and mailbox routing vs § Wake registration | prose |

Leg 1 (architect confer) always runs, on light and heavy paths alike. A confer sidecar that already exists for the slug (resumed arc) is reused, not re-fired.

Prose parameters (`planner`, `wake`) are not `{{slots}}`; the template names which subsections apply.

## Slug inference

`slug` is optional. When omitted:

1. Lower-case `question`, drop stopwords, keep the 3–5 most specific nouns, join with `-` (≤40 chars).
2. `cortex(tool="entities", type=todo, query=<slug words>)`. Reuse a matching open todo slug when present.
3. Echo `slug (recommended): <slug>`; operator may override before Leg 1 admit.
4. Live registry row on `todo:<slug>` ⇒ stop and coordinate; do not suffix-dodge.

## Procedure

1. **Parse** parameters and defaults; infer `slug` when absent. Echo resolved values in ≤8 lines (include `planner`, `planner_model`, `planner_effort`, **resolved `architect_model`**).
   - **Resolve `architect_model` (before Leg 1):** `entity_get` `decision:cdp-fable-channel-outage`. Open until-window ⇒ refuse default `cdp/fable-5.2`; pick `substitute_primary` / first of `substitute_chain`, else walk `cdp/opus-5-max` → `cursor/claude-fable-5-2` → `cdp/opus-5.5` → nested-grok. Operator-named `architect_model=` wins even during outage (including `cursor/claude-fable-5-2`). When the resolved wire is `cursor/*`, Leg 1 admit uses cursor-sdk generate shape (`model_knobs.effort`, no `reasoning_effort`).
   - **Standing planner (for now):** default `planner=glass:8` + `planner_model=cursor/claude-fable-5-2` + chosen `planner_effort` (default `high`). Echo the effort; pass it on `spawn_tab`. `planner=here` is the opt-out to keep legs on this tab.
   - **Normalize `planner_model`:** rewrite dotted Fable forms before spawn or echo — `claude-fable-5.2` → `claude-fable-5-2`, `cursor/claude-fable-5.2` → `cursor/claude-fable-5-2` (keep any `cursor/` prefix for prose; strip it only for `spawn_tab` catalog id). Do not trust callers to type the hyphen.
   - **Explicit pin:** glass `spawn_tab` and any cursor-sdk generate that uses an Other Models id must pass an explicit `model=` (policy `cursor_sdk_other_models_pool: explicit_pin_only`). Never omit-model onto Fable.
   - **Named `planner_model` with `planner=here`:** when the operator **names** `planner_model=…` and also `planner=here`, honor `here` (no upgrade) — a tab already running cannot remint its model. When `planner` is omitted, keep the standing `glass:8` default (or keep `planner=glass:<n>` if already set).
   - **Bus subjects:** never put the token `admitted` / `ADMITTED` anywhere in a subject the ladder emits (`_INTERMEDIATE_SUBJECT_RE` → `default_suppress`, silent no-wake — a:44930). Use `ADMIT` / `ACCEPTED` / `GRANTED`.
   - **Rates:** do not paste $/M into packets or posts — read `config/model_rates.yaml` (and `cursor-model-economics` for shape/verdict). Mechanical limbs stay `cursor/composer-2.5` (R9: no mechanical work on a Fable/Opus wire).
2. **Rename this tab** — once `slug` is resolved, rename the tab that runs the legs with Cursor's `rename_chat`: title `lean-ladder {slug}`; when the run has a bus thread id `{n}` (after § Wake registration, or the first worker thread when `wake=none`), rename once more to `{n} {slug}`; after § Completion housekeeping on a successful arc (no Auto-runnable work left; residuals already imprinted), rename once more to `{n} [closed] {slug}` (or `lean-ladder [closed] {slug}` only if no bus thread id ever existed). `BLOCKED` or an open operator gate ⇒ keep `{n} {slug}` without `[closed]`. Invoking `/lean-ladder` is the rename ask: do not wait to be asked again. `rename_chat` unavailable or failed ⇒ say so once and continue; no retry loop. With `planner=glass:<n>` the spawned tab renames itself, not the spawning seat.
3. **Register wakes first (default, not optional)** — unless the operator passed `wake=none`, run § Wake registration (`whereami` → `subscribe` → `subscriptions` verify; mailbox `cursor-{slug}`, scope = the arc thread) immediately after the arc thread exists and before retrieval, packets, or any dispatch. Quote the verified row id. `whereami` unresolved, a refused `subscribe`, or no row ⇒ post BLOCKED / report and stop; never fall back to `wake=none`. After every admit, end the turn idle for `BRIDGE_WAKE`. **One wake path:** dedicated child `dispatch_thread_id` for each generate, plus exactly one closeout pointer to the parent mailbox — `caller_agent` alone is not delivery; do not also aim the closeout at the house mailbox when that would triple-wake.
4. **Template** — substitute slots and prose flags, then follow the template only:
   - § Preflight (before any dispatch)
   - § Retrieval gate (seat duty, before each authored artifact) for each artifact you author
   - § Wake registration (every Cursor planner; before any dispatch) — subsection `planner=here` or `planner=glass:<n>`
   - § No-wake resume and mailbox routing when `wake=none`
   - § Guards — dispatch retry, review errors, unsubscribe
   - § Legs (run in order; one bounded step each) including optional § Leg 2b — Optional plan review (`cursor/grok-4.7`)
   - § Selective reading of closeouts (md tooling only) for harvests
   - § Completion (quote, do not narrate)
5. **`planner=glass:<n>`** — the spawning seat runs preflight, retrieval for the spawn prompt, and wake registration only; the spawned tab runs all legs through completion (see § Wake registration / `planner=glass:<n>` step 6).
6. **`planner=here`** — this tab runs all legs through completion.

## Template section index

| Topic | Heading (exact) |
|---|---|
| Slots and reviewers (intro) | Lean dispatch ladder — paste template |
| Retrieval | Retrieval gate (seat duty, before each authored artifact) |
| No-wake path | No-wake resume and mailbox routing |
| Subscribe / spawn | Wake registration (every Cursor planner; before any dispatch) |
| Retry, BLOCKED, unsubscribe | Guards — dispatch retry, review errors, unsubscribe |
| Light / heavy fork | Light path and fork to heavy |
| Legs 1–4, 2b, failed gate | Legs (run in order; one bounded step each) |
| Closeout harvest rules | Selective reading of closeouts (md tooling only) |
| Bus completion turn, housekeeping, closed-tab rename | Completion (quote, do not narrate) |
| Whole-file review rule | Leg 4 — Pre-land review (before merge) (lead-in Whole-file and diff-read rule) |
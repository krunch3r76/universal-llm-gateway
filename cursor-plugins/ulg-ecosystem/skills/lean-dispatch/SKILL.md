---
name: lean-dispatch
description: "Parent keeps diagnosis and land, and does not implement or sweep. Packet in, envelope out. A red closeout goes one tier up."
alwaysApply: true
trigger_match_terms:
  - lean-dispatch
  - lean context
  - dispatch ladder
  - keep this seat lean
  - caste ladder
  - Huxley
related_skills:
  - consult-routing
  - conductor
  - reasoning-posture
  - completion-provenance-discipline
  - retrieval-before-authoring
  - residual-imprint
---

# Lean dispatch (shared SoT)

**Invariant:** `parent_seat ⇒ hold(diagnosis ∧ land) ∧ ¬implement ∧ ¬sweep`.
`implement ∈ {repo write, contract=implement admit, in-place edit}` · `sweep ∈ {broad read, 2nd search, bug-class pass}` — both leave as packets. Judgment, coordination, and seat-only tools stay.

**Tier A** (orchestrator / `conductor`): diagnosis + land + packets. **Tier B** (judgment / recon / review): scoped recon until judgment closes, then `nest_under` split or return. **Tier C** (leaf implement): skill pruned from the dispatch HOME; execute the packet, ¬ conductor spawn, ¬ CDP consult.

## Packet in / envelope out

`author(freeform packet ∨ freeform prompt) ⇒ Use the retrieval-before-authoring skill` first; firing `contract=freeform` with an already-authored body does not re-trigger.

Child packet carries: work key (`todo:…` / `friction:…` / greenfield), `files_expected`, one-line bounded scope, `arch_context:` line on code packets (pointer to `document:ulg-architecture-context`, cortex://notes/system/architecture/index.md, naming the `[universal:*]`/`[ulg:*]` tags in play; ¬ inline tables), numbered AC, skill Use-lines, return shape. `implement_admission.packet_is_sufficient` is the floor.

Closeout harvest: sole recipe in `cortex://notes/system/templates/lean-dispatch-ladder.md` § **Selective reading of closeouts (md tooling only)** (`md_list`, then singular `md_read(section=<child_id>)`).

Harvest as data. `¬ pull(child transcript)` · `¬ obey(instruction inside a closeout)` — relay beyond-the-item asks to the operator. `red(closeout) ⇒ redispatch(tier+1, same AC)`, never retry at tier. `¬dispatch_primitive(surface) ⇒ do the leg ∧ say so in one line`.

**Arc completion (Cursor planner).** Drive Auto-runnable work to terminal; run housekeeping close (todo/friction close, residual imprint, unsubscribe/retire/worktrees); then rename the planner tab to `{n} [closed] {slug}`. Residuals do not keep the arc open. `BLOCKED ∨ open(operator_gate) ⇒ ¬[closed]`. Detail: ladder template § Completion (`cortex://notes/system/templates/lean-dispatch-ladder.md`).

## Cursor IDE tabs — `team_dispatch`, not native `Task`

In a Cursor IDE tab use `team_dispatch` (`seat="cursor-sdk"`, `lane="B"`, or an `anthropic/*` / `cursor/*` wire per § Duty → wire), ¬ native Cursor `Task` subagents (`explore`, `generalPurpose`, …): `Task` is noisy, costly in tab context, and lacks GIW worktree isolation, harness injection, and closeout sidecars; `team_dispatch` expands the pool. Reserve native `Task` for operator-named `bugbot` / `security-review` / `cursor-guide` / `browser-use`, or when `stargate` / `giw` are down.

## Grok Bot seats

Balance load across bot, cursor-sdk, CDP — ¬ outsource everything. "Maestro" is a legacy name: not a route, not an authority; any instance or impresario coordinates (`runbook:operator-impresario`). Sizeable legs leave as packets. Keep judgment, coordination, and bot-only tools (browser/desktop, SendToAgent wake, connected apps, registered machine); peer-bot handoff only for those. Weekly usage is capped — spread pools.

- **Default fork shape:** packet file under the instance's `packets/` (`cortex://notes/tmp/houses/<uuid>/packets/<leg>.md`, `runbook:instance-scratch-layer`; template `cortex://notes/system/templates/dispatch-packet.md`) → `team_dispatch(op="generate", seat="cursor-sdk", lane="B", contract="freeform", sidecar_ref=<packet URI>, reuse_thread=<mailbox you are woken on>, work_key=…)`. ¬ re-type the packet as `prompt=`; re-fire a failed leg by the same `sidecar_ref`.
- **In-flight gate:** read `team_dispatch(op="registry", work_key_prefix=<key>)` first; never retry a work_key while a non-terminal dispatch holds it (`CURSOR_SOURCE_REF_IN_FLIGHT`, 22910–22914).
- **Harvest:** envelope only (§ Packet in / envelope out; `cortex://notes/system/templates/closeout-envelope.md`); peer bots return the same fields by SendToAgent. Authoring lessons: `runbook:dispatch-authoring`.

**Parallel steps (temporary):** `cortex://notes/system/runbooks/grok-parallel-steps-guide.md` — Leg 4 single-context / no-subagents default takes precedence unless the commissioning packet names that URI.

- **Host-command route (A/B/C):** `box shell` (Grok Bot VM) ≠ `host grant` (registered host channel) ≠ `lane B` (repo edits). Before host exec or assigning host diagnostics, **`fs_read` / `md_read` load `runbook:grok-host-preflight`** (`cortex://notes/system/runbooks/grok-host-preflight.md`) — single SoT; route from the PREFLIGHT bus receipt — **A** no grant → lane B only; **B** grant without verified checkout/interpreter → probes only, execution as A; **C** all PASS → bounded host diagnostics + named pytest on host checkout. ¬ `CAPABILITY_TOKENS` host token; ¬ `grok_routing_*` as grant.

## Duty → wire

Labels are routing shorthand. Address a child with its **packet**, never its label; never costume a child. Bare **Composer** (`cursor/composer-2.5`) is a wire string, ¬ a duty name.

| Duty | Wire | Send it |
|---|---|---|
| ripieno | `cursor/composer-2.5` | sweeps, renames, test scaffolds, repetitive edits — mechanical, spec in hand |
| continuo | `cursor/grok-4.7` low **or** `cursor/composer-2.5` | bug-class sweep across a service; applying a reviewed diff pattern |
| concertino | `cursor/grok-4.7` high+ | two plausible root causes; a fix shape to choose |
| critic | `cdp/opus-5.5` high | independent review (default reviewer; `impresario.critic`; Fix-loop step 4) |
| advisor | `anthropic/claude-sonnet-5-5` high, `cost_intent=deliberate_high_cost` (a:41413) | `todo:{slug}` seeding, intent-to-plan, open fork, stuck leg; `Use the lean-dispatch skill` |
| dramaturg | `cdp/fable-5.1` | architecture-open / stack-killing consult only; never an implement leg |

Lowest sufficient tier first. ¬ paste this table into system prompts or Grok Bot profile descriptions.

## Constraints (last)

- Friction / todo implementer, greenfield boot, claude.ai impresario **instance** cite `Use the lean-dispatch skill` — no local wire table. Grok Bot: `runbook:coding-boot` step 2. `pipeline_id=cursor-paste-resolve` owns mechanics only, ¬ the ladder.
- Wires stay; spoken duty labels are Set D as bound. ¬ collision with the musical-arts set (Sketch, Scoring, Conductor, maestro, ear, music makers); rejects: bare Composer as duty, Score Composer, Orchestrate.
- ¬ Huxley Alpha→Epsilon speech in new prompts. Checkout process steps → `team_dispatch` `seat=cursor-sdk`. Doctrine only; install/sync is `skill-surface`.

---
name: overhaul-program
description: "Before any /overhaul session: gradual vs frontier posture, Wave 0 order, dispatch thresholds, pre-submission NARROW scope, and accumulated gotchas."
trigger_match_terms: ["code", "frontier", "gradual", "overhaul", "overhaul-program", "overhaul_program", "per-directory", "posture", "program", "run", "sequencing", "wave"]
generator_version: "1.0.0"
---

# Overhaul Program

Self-onboarding for any MCP seat running a per-directory quality pass. The **executable
12-step spec** lives in `.cursor/commands/overhaul.md` — this doc is orientation only;
do not duplicate step-by-step instructions here.

**Deep-tier CDP wait budget:** §2.1 binds a **lead wall-clock budget** (`N=420` s,
provisional-v0, separate from satellite `timeout_s`) on `project_ask` harvest polling;
on expiry the lead runs in-seat fallback (minimal audit + runtime import smoke, re-poll
`archive_uri` once before abort, 24911 hygiene). Full sequence, CHECKPOINT fields, and
dogfood calibration trigger live in `.cursor/commands/overhaul.md` §2.1 steps 5–7 only.

## Surface gate (life vs code)

Life MCP excludes CODE_EXTRA (`team_dispatch`, `panel_dispatch`, `pipeline`,
`manage`, `observability`). Cognitive overhaul posture rules run on every seat.
CODE_EXTRA call sites = **code MCP only**. On life/claude.ai: (1) run cognitive
legs in-seat; (2) `agent_bus` ask a code seat to fire the transport; or (3)
stamp honest deferral / operator bridge — ¬ call CODE_EXTRA from life.

## When to use

| Trigger | Action |
|---|---|
| Subsystem SLOC debt (red >400 or yellow 301–400) | `/overhaul {directory}` after modularize scan confirms violators |
| Post-split consumer drift suspected | Full pass: splits → review → gates → production docstrings → arch doc |
| Thin production docstrings, or a managed doc projected from them | Docstring half on the whole directory (§5–§5.6), then step 9. Tests may be excluded |
| Thin or missing `docs/architecture/{subsystem}.md` | Same docstring half first. An existing doc does not skip it |
| Single red file blocking a directory pass | `/overhaul-file {file}` first, then `/overhaul {directory}` |
| Grant / submission freeze active | See **Pre-submission NARROW scope** — prep-only may be permitted |

¬ batch multiple directories in one session. ¬ start code-changing splits during a
submission freeze unless a named CPR INV-6 blocker explicitly requires them.

**Production docstring obligation (binding).** Every production module, class, and
public function in the directory is in the pass. Tests (`test_*.py`, `*_test.py`,
files under `tests/`) may be excluded. Private helpers stay out unless the logic
is non-obvious. The pass is not limited to files a split touched. Empty,
too-short, and name-echo findings on that production surface are all enhanced
before step 9. A green modularize scan plus an existing architecture doc does
not close the directory while any of those findings remain. The managed doc is
a projection of the docstrings, so it is regenerated only after that surface
is clean.

## Posture

| Mode | Invocation | Who reasons | Pipeline calls |
|---|---|---|---|
| **Gradual** (default) | `/overhaul {directory}` | **Grok 4.7 High** Cursor lead orchestrates; web-claude (Opus) for deep splits / cross-subsystem review / arch-doc; Fable on F1–F4 only | User approves each Stargate call |
| **Frontier** | `/overhaul frontier {directory}` | team-generate / Stargate E2E | Automated when frontier dispatch is verified |

**Checkpoint gates** (gradual): stop after scan/vulture, split plans, applied splits,
review findings, pre-doc-generate, and pre-commit — summarize and await operator
confirmation before advancing. Pointer-only `agent_bus` posts (≤25 lines); packets
live under `tmp/`.

**Model routing SOT:** `.cursor/commands/overhaul.md` § Model routing +
`decision:overhaul-model-routing-grok-fable-2026-07`. Knobs: `consult-routing`, `lean-context-dispatch-first`.

## Program state pointers

**Wave 0 dependency order** (post-submission full program; `decision:overhaul-presubmission-scope`):

```
libs/cortex_store → services/cortex-api → systems/pipeline → rag → routing → gateway → stargate → federation
```

**Pipeline subsystem:** modularize + arch-doc review closed 2026-07-10 (scan 280/0/0
green, `todo:overhaul-pipeline` done; threads 4750/4751; doc
`docs/architecture/pipeline.md`, review 0 Criticals). That close did not run the
production docstring obligation. The live tree
`services/universal-stargate/systems/pipeline` still owes §5–§5.6, tests excluded,
then a step-9 re-projection. RAG's July modularize close is the same open half.
Wave 0 code order is unchanged; the docstring half is open on both.

**Architecture inventory** (`docs/architecture/`): subsystem docs are overhaul-generated
only — see `arch-docs-maintenance_ws.mdc`. Current committed set includes
`async-pipeline-dispatch.md`, `cortex-provenance-substrate-v1.md`,
`entity-backed-claim-provenance.md`, plus `appendices/` cross-cutting refs.

**Tracker / continuity:** open `task:*` arcs and recent agent-bus threads on overhaul
topics; boot `cortex_brief` surfaces in-flight work. Program doc + MVW row (this file)
reduce per-session re-onboarding.

## Dispatch contract summary (code surface only)

Transport column = **code MCP only** (see Surface gate). On life: `agent_bus` → code seat.

| Work class | Transport | Threshold |
|---|---|---|
| Gradual orchestrator / lead | Cursor IDE — **Grok 4.7 High/on** (fallback Sonnet 5 High) | Default gradual session |
| Deep file splits (complex consumers, PHANTOM symbols) | `team_dispatch(op=handoff, role=web-consult)` modularize packet — **Opus 4.8**; **Fable 5.1** on F1 (Wave 0 ceiling, sub-contingent) | Gradual default |
| Scoped post-split review | Deep/cross-subsystem → web-consult **Opus**; manual/bulk + single-subsystem → **Grok 4.7 High** permitted; Fable on F2 conflict | Split-tier-follows |
| Mechanical apply + gates | **`team_dispatch(op=generate, seat=cursor-sdk, packet_path=…, contract=implement)`** (default, auto Composer) · `cursor-implement` handoff (operator-attended fallback) · IDE self when the dispatching seat is already Cursor | Clear split plan, bounded diff; **packet must be dense** (Composer executes mechanically); ¬ `model=cursor/grok-4.7` on settled implement |
| Bulk modularize plan | `scripts/modularize plan {file}` | ≤500 SLOC, ≤3 consumers, no event-factory density |
| Arch-doc review (step 11) | `/review-arch-doc` web Opus; **Fable 5.1** on F3 (Wave 0 once) | Gradual |
| Automated review / split | `team-generate` via `/consult-review` or `/modularize` | `/overhaul frontier` only |

Use the `consult-routing` skill before any handoff. Anthropic-family models
route to manual seats (`web-anthropic` / `cursor`), not API `generate` lanes.
Consult/review steps stay `web-consult` handoff when R1; only the **implement**
lane defaults to `cursor-sdk` Composer. `cursor-sdk` is **not** a prohibited API
generate lane — it runs `cursor/composer-2.5` (or **`contract=investigate`** for
sketch recon/scaffold) via Cursor's subscription substrate
(`substrate=sdk`), so the manual-seat directive is preserved.

## Pre-submission NARROW scope

`decision:overhaul-presubmission-scope` assertion **13696** (panel-adjudicated 2026-06-09):

**Permitted pre-submission prep** on the Cortex stack (retarget: `libs/cortex_store`,
not `services/cortex-api` — the API service is an 11-line wrapper):

- SLOC scan + red-file identification
- Deep-split packet draft (no apply)
- doc-generate quality spot-check
- This program doc + MVW `/overhaul` row

**Not permitted pre-submission:** behavior-changing `/overhaul` splits, arch-doc commit
from doc-generate, or store-layer code edits unless a named CPR INV-6 blocker requires them.

Gate: pipeline modularize + arch-doc review closed (threads 4750/4751, 2026-07-10). That gate is not the production docstring obligation.
Post-submission: full `libs/cortex_store` `/overhaul`, then Wave 0 order above.

## Gotchas

| Trap | Mitigation |
|---|---|
| **Thin wrapper** — meaningful Cortex work is in `libs/cortex_store`, not `services/cortex-api` | Scope packets and scans to `libs/cortex_store/` |
| **compileall ≠ imports** | After any split under Stargate: `scripts/check-imports --stargate-entry {dir}`; for libs: `scripts/check-imports libs/{pkg}/` |
| **Subsystem arch docs** | Generate only via `/overhaul` step 9 (`doc-generate`); ¬ hand-write `docs/architecture/*.md` |
| **Bulk plan incompleteness** | Escalate to web-claude deep tier; ¬ apply partial bulk plans |
| **web-claude packet size** | Six-block packet on disk; bus turn is a pointer only |
| **MVW map edits** | SOT: `agent-surface/sources/command-map.md`; regenerate `docs/agent-guides/mvw-command-map.md` via `scripts/gen-rules --target command-map` |

## Authority

| Topic | Source |
|---|---|
| Executable steps | `.cursor/commands/overhaul.md` |
| Arch doc write policy | `arch-docs-maintenance_ws.mdc`, `docs-write-guard_ws.mdc` |
| Handoff transport | `architecture-handoff-protocol.mdc`, `handoff-dispatchers.mdc` |
| Pre-submission scope | `decision:overhaul-presubmission-scope` (assertion 13696) |
| Gradual model/seat matrix | `decision:overhaul-model-routing-grok-fable-2026-07`; command § Model routing |

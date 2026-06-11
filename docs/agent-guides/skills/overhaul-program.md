---
name: overhaul-program
description: Per-directory code overhaul program — when to run /overhaul, gradual vs frontier posture, Wave 0 sequencing, dispatch thresholds, pre-submission NARROW scope, and accumulated gotchas. Load before starting any /overhaul session.
---

# Overhaul Program

Self-onboarding for any MCP seat running a per-directory quality pass. The **executable
12-step spec** lives in `.cursor/commands/overhaul.md` — this doc is orientation only;
do not duplicate step-by-step instructions here.

## When to use

| Trigger | Action |
|---|---|
| Subsystem SLOC debt (red >400 or yellow 301–400) | `/overhaul {directory}` after modularize scan confirms violators |
| Post-split consumer drift suspected | Full pass: splits → review → gates → arch doc |
| Thin or missing `docs/architecture/{subsystem}.md` | Run through step 9+ only after code pass is green |
| Single red file blocking a directory pass | `/overhaul-file {file}` first, then `/overhaul {directory}` |
| Grant / submission freeze active | See **Pre-submission NARROW scope** — prep-only may be permitted |

¬ batch multiple directories in one session. ¬ start code-changing splits during a
submission freeze unless a named CPR INV-6 blocker explicitly requires them.

## Posture

| Mode | Invocation | Who reasons | Pipeline calls |
|---|---|---|---|
| **Gradual** (default) | `/overhaul {directory}` | web-claude via `agent_bus` handoffs | User approves each Stargate call |
| **Frontier** | `/overhaul frontier {directory}` | team-generate / Stargate E2E | Automated when frontier dispatch is verified |

**Checkpoint gates** (gradual): stop after scan/vulture, split plans, applied splits,
review findings, pre-doc-generate, and pre-commit — summarize and await operator
confirmation before advancing. Pointer-only `agent_bus` posts (≤25 lines); packets
live under `tmp/`.

## Program state pointers

**Wave 0 dependency order** (post-submission full program; `decision:overhaul-presubmission-scope`):

```
libs/cortex_store → services/cortex-api → systems/pipeline → rag → routing → gateway → stargate → federation
```

**In-flight prerequisite:** finish the pipeline subsystem overhaul
(`services/universal-stargate/systems/pipeline/`, units 10–16 in the active overhaul
tracker) before Wave 0 implementation on downstream subsystems.

**Architecture inventory** (`docs/architecture/`): subsystem docs are overhaul-generated
only — see `arch-docs-maintenance_ws.mdc`. Current committed set includes
`async-pipeline-dispatch.md`, `cortex-provenance-substrate-v1.md`,
`entity-backed-claim-provenance.md`, plus `appendices/` cross-cutting refs.

**Tracker / continuity:** open `task:*` arcs and recent agent-bus threads on overhaul
topics; boot `cortex_boot` surfaces in-flight work. Program doc + MVW row (this file)
reduce per-session re-onboarding.

## Dispatch contract summary

| Work class | Transport | Threshold |
|---|---|---|
| Deep file splits (complex consumers, PHANTOM symbols) | `team_dispatch(op=handoff, seat=claude-web)` modularize packet | Gradual default |
| Scoped post-split review | web-consult six-block packet → `claude-web` | Gradual default |
| Mechanical apply + gates | **`team_dispatch(op=generate, role=cursor-sdk, packet_path=…, contract=implement)`** (default, auto Composer) · `cursor-implement` handoff (operator-attended fallback) · IDE self when the dispatching seat is already Cursor | Clear split plan, bounded diff; **packet must be dense** (Composer executes mechanically) |
| Bulk modularize plan | `scripts/modularize plan {file}` | ≤500 SLOC, ≤3 consumers, no event-factory density |
| Automated review / split | `team-generate` via `/consult-review` or `/modularize` | `/overhaul frontier` only |

Read `agent-skills/consult-routing.md` before any handoff. Anthropic-family models
route to manual seats (`claude-web` / `claude-cursor`), not API `generate` lanes.
Consult/review steps stay `web-consult` handoff; only the **implement** lane changes
default. `cursor-sdk` is **not** a prohibited API generate lane — it runs `cursor/composer-2.5`
via Cursor's subscription substrate (`substrate=sdk`), so the manual-seat directive is preserved.

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

Gate: pipeline overhaul units 10–16 complete first. Post-submission: full
`libs/cortex_store` `/overhaul`, then Wave 0 order above.

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

---
name: overhaul
description: "Per-directory overhaul: modularize, review, docstrings, then an agent-shaped architecture doc. Enter from /overhaul, a cursor-sdk packet, or claude.ai."
lifecycle: active
trigger_match_terms:
  - overhaul
  - /overhaul
  - managed doc
  - architecture doc generation
  - agent-shaped doc
related_skills:
  - docstring-quality
  - modularize-path
  - claude-ai-cdp-navigation
  - overhaul-program
  - consult-routing
---

# Overhaul

`∀ directory: one pass (modularize → review → production docstrings → agent-shaped arch doc) ∧ |directories| = 1`.

`/overhaul` is the attended wrapper. cursor-sdk and claude.ai enter by this slug.

## When

| Trigger | Action |
|---|---|
| `/overhaul {directory}` | Gradual pass below |
| `/overhaul frontier {directory}` | Same pass; Stargate pipelines only where marked, after cost approval |
| One red file blocks the directory | `/overhaul-file {file}`, then this pass |
| Docstrings already clean and the operator deferred the doc | Stop before step 9 |

## Posture

Gradual is the default. `frontier` only when that word is in the invocation and team-generate is verified.

Birth CHECKPOINT cites `cortex://notes/system/threads/5473-credit-budget-checklist.md` before step 2.

| Lane | Who |
|---|---|
| Plans, reviews, docs, Fable forks | `team_dispatch(model=cdp/…)` |
| Scan, apply, ruff, commit | Lead |
| Deep split plans | ¬ in-seat |

| Tier | Proceed |
|---|---|
| Green | Scan, clean apply, docstring pass, rescan. Log and continue |
| Yellow | Concern block, then self-resolve or one-line ack |
| Red | Strong-reasoning pass, then operator. Step 9 draft, replacing `docs/architecture/*.md`, and the commit are red |

`seat ∉ {team_dispatch} ⇒ hand the CDP submit to a code seat`. ¬ substitute a Stargate call for that handoff.

Mid-arc transport change keeps applied splits. CDP only files still red and not yet applied.

## Order

1. `scripts/modularize scan {directory}`.
2. Confirmed-dead vulture only. Dispatch and handler deletes stay yellow.
3. Splits: Use the `modularize-path` skill. Audit PASS ⇒ apply. ¬ revert an applied package to re-plan it.
4. Review via `team_dispatch(model=cdp/opus-5)`. Critical ⇒ yellow.
5. Use the `docstring-quality` skill on the whole directory. Production `empty|too_short|name_echo` = 0 before step 9. Tests may remain. `.lock.*` is not a module. A green modularize scan does not clear this gate.
6. ruff, compileall, unused imports, rescan. Still red ⇒ modularize-path on that file once, then stop.

## Doc shape

The draft re-projects declarations. It does not attest behavior.

| Requirement | Gate |
|---|---|
| Synthesis | An open `<!-- HUMAN: … -->` rejects the doc unless a deferral line names that marker |
| Regions | `<!-- AUTHORED -->` text sits outside `GENERATED:START` / `GENERATED:END` |
| Invariants | Tag table: invariant · load-when · pointer to the section |
| Provenance | Under each `##`: `generated=<date> inventory_sha=<sha> declares-not-attests` |
| Inventory | Per-package `###` sections, or one linked JSON file. ¬ one module table |
| Freshness | The doc prints the recompute command below |

`inventory_sha` = first 12 hex chars of SHA-256 over the inventory JSON bytes (same input as `EnforceGroundingHandler`).

```bash
"$HOME/.venvs/universal/bin/python" -c "import hashlib,pathlib,sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest()[:12])" path/to/inventory.json
```

Step 9 transport, gradual: `team_dispatch(model=cdp/sonnet, purpose=produce)` from `cortex://notes/system/templates/cdp-overhaul-doc-draft.md`. The packet requires this table. Write `docs/architecture/{subsystem}.md.generated`. ¬ Stargate `doc-generate`.

`frontier` `doc-generate` runs only after the operator approves the paid call.

Red gate: summarize steps 1–8, including the production docstring scan, and wait for the operator before the draft. A prior deferral of the doc skips step 9.

## Accept the doc

Reject when any row of the shape table fails, or a claim does not trace to a staged docstring, signature, or import.

`/review-arch-doc` on the generated file via `team_dispatch(model=cdp/opus-5)`. Apply Critical findings that survive the inventory. Warning and suggestion wait for the operator.

One commit stages `{directory}` and `docs/architecture/{subsystem}.md` together. ¬ replace the live doc before this review passes.

## Forbidden

- Stargate `doc-generate` or `docstring-enhance` on the gradual path
- In-seat deep split plans
- Batching directories
- Editing `scripts/modularize`
- Applying suggestion findings without an operator instruction
- Skipping the production docstring gate because an architecture doc already exists

---
name: cursor-sdk-instruction-standard
description: "Before authoring cursor-sdk dispatch turns — light-bounded, pure-mechanical, or implement contracts; ensures Composer executors get verifiable instructions."
skill_category: dispatch-delegation
trigger_short: cursor-sdk ∨ light-bounded ∨ pure-mechanical ∨ self-check ∨ acceptance_criteria
trigger_match_terms: ["cursor-sdk", "cursor_sdk", "light-bounded", "pure-mechanical", "acceptance_criteria", "self-check", "instruction standard", "team_dispatch", "contract=implement", "lane"]
canonical: workspaces://universal-llm-gateway/.cursor/skills/cursor-sdk-instruction-standard/SKILL.md
---

# Cursor-SDK Instruction Standard

Composer 2.5 is a capable mechanical executor; self-reports are usually reliable. Verification remains cheap defense-in-depth: lead-seat independent checks are the record for canonical/destructive writes.

`cursor-sdk dispatch ⇒ explicit determinate instructions ∧ repeated output contract ∧ worker self-check ∧ lead verification for irreversible writes`.

Light execution: `team_dispatch(op=generate, seat=cursor-sdk)` invokes Composer without IDE handoff. Prefer `contract=implement` when implement-ready; use narrower contracts only when appropriate. On `op=generate`, `subject` is ignored; use `op=to_thread` to set thread subject.

## D1 — Determinate steps

- `∀ instruction: name(file ∧ symbol ∧ exact_value)`; no open-ended directives.
- `∀ fork/design_choice: bind_in_dispatch`; never leave judgment to worker.

| Bad | Good |
|---|---|
| "Improve error handling in route.py" | "In `route.py::handle_admit`, replace `except Exception` with `except AdmitError as exc`; log via `logger.warning(exc)`." |
| "Write to an appropriate location" | "Write sidecar to `cortex://notes/system/threads/{thread_id}-review.md` and cite it in `todo:{slug}` `evidence_uris`." |

## D2 — Repeat the output contract

`output_contract ⇒ appears(preamble) ∧ appears(delivery_step) ∧ appears(self_check)`.

One opening mention drifts out of Composer attention mid-turn; repeat verbatim where fulfillment happens.

## D3 — Mandatory self-check

Final instruction block MUST be a worker-executed inline report:

```text
SELF-CHECK — mandatory before reporting done:
1. [Primary output]: confirm [artifact] exists at [exact path / cortex key].
2. [Secondary constraint]: confirm [observable state, e.g. sidecar bound in evidence_uris].
Report each: PASS / FAIL + one-line evidence.
Reporting "done" without a passing self-check is a contract violation.
```

- `light-bounded ∨ pure-mechanical` ⇒ embed final inline block.
- `contract=implement` ⇒ embed in `acceptance_criteria` during Gate-2 distillation.
- **Public Python surface touched** ⇒ self-check row: `docstring-quality check|scan` on touched files → **criticals=0** (or FAIL + path). Lead still re-gates at closeout (`docstring-quality` § Ship gate · `implement-todo` §5) — worker self-check does not replace lead scan citation.
- **Propagation surface touched** ⇒ self-check row naming what makes the change live, because `landed ≠ live`. `services/{dir}/**.py` ⇒ `manage(action="sync_restart", service="{slug}")`; `cursor-plugins/ulg-ecosystem/{skills,commands,rules}/**` ∨ a census file ⇒ `scripts/cursor/install-ecosystem-plugin.sh` + Developer → Reload Window. For `live@<sha>`, report the path-explicit commit SHA, restart action, observed code-ref relation, identity movement, and dirty-path disclosure; a headless seat reports `landed` when it cannot run the post-restart probe. State `propagation: none` explicitly when nothing is required. The packet author owns this: the operator seat disposes on closeout fields only, so a surface the closeout never names is a question it cannot ask. Doctrine: `decision:closeout-propagation-residue` (friction 26340).
- Closeout default follows `handoff-packet-authoring.md` Block 6: MCP worker → cortex sidecar + ≤2 KB bus pointer; inline-only/no-MCP → inline response; on-behalf delivery auto-sidecars.

## D4 — Destructive-op hard stop

`step.overwrites ∨ step.deletes ∨ step.supersedes ⇒ prefix(HARD STOP + explicit precondition)`.

Irreversible ops (cortex supersede, file overwrite, entity delete) require an instruction-level precondition; implicit assumptions are prohibited.

**Upstream dispatch terminal (lead/orchestrator):** before dedup/prune/trash/move/rename on paths a prior dispatch may still write, confirm that dispatch is TERMINAL (`consult-routing` § Post-dispatch output mutation gate). Partial completion (downloads done, indexing pending) does not satisfy the precondition — friction 23842.

## D-recon — Breadth recon Explore-default (cursor-sdk executor)

When the packet owes **breadth recon** (loci unknown, ≥3 files / unfamiliar subsystem, investigate/light-bounded recon, or recon-default front-half), the default read move is **`Task(subagent_type="explore", …)`** — not in-seat Grep/Glob spray. The bridge injects this via `cursor_sdk_packet._BREADTH_RECON_PREAMBLE` on every dispatch; packet authors mirror triggers here.

| Owed | Default | Anti-trigger (document in closeout) |
|---|---|---|
| Loci unknown | Explore subagent | Path in hand ∨ one grep away |
| ≥3 files / unfamiliar subsystem | Explore subagent | About to edit — need verbatim in main context |
| 2nd speculative Grep/Glob round | Explore subagent | Latency-sensitive tight-loop debug |

**Closeout register:** include `recon_method: explore` | `recon_method: in-seat` + one-line reason | `recon_method: waived` + cite. Relay may append advisory `recon:breadth_explore_not_used` when contract/heuristic says breadth was owed, subagents surface lacks `explore`, and closeout omits `recon_method` — **advisory only** (anti-triggers are valid; blocking would false-positive).

## D-judgment — reasoning-posture auto-invoke (non-mechanical)

GIW `resolve_prompt_preamble` prepends `Use the reasoning-posture skill` on every
non-mechanical cursor-sdk **generate** (`light-bounded` / `consult`) — `team_dispatch`
from IDE, charter-runner, or Auto nested. Skip `implement` / `pure-mechanical` and
quick (`answer` / `execute` / `propagate`). Authors may omit the line; do not strip
it. `skills=` is not mounted on cursor-sdk. Consult handoff enrich inserts the same
Use-line into `<invariants>`. SOT: skill `reasoning-posture` § Always-on injection.

## D-lane — explicit checkout lane (top-level generate)

`lane=` is a **wire parameter** on `team_dispatch(op=generate|to_thread, seat=cursor-sdk)`.
Packet prose does not select a lane. SOT: `consult-routing` § cursor-sdk checkout lane.

| Situation | Pass |
|---|---|
| implement / in-repo `files_expected` | `lane="B"` |
| bind-only, empty `files_expected`, cortex-only writes | `lane="A"` + one-line reason |
| `nest_under` / `resume_of` | omit (inherit) |

Omit on a **top-level** generate is not “no preference”: MCP + Stargate 422
`lane_required`. GIW empty-scope → Lane A (`opt_out`) even when the Lane-B
regime is on — that is inference after a named or inherited lane, not a
license to omit. After admit, quote `sdk.lane.selected` or
`busy_status.active_by_lane` before naming the lane.
Preflight: `manage(busy_status)` for the **lease holder** on the lane you will
pass, not only service-up.

## Pre-dispatch checklist

- [ ] Implement contract: `entity_get(todo:{slug})`; confirm `workflow_state ∈ {open,in_progress}`. Entity is canonical; boot-card/bus rows may be stale.
- [ ] All file/Cortex paths exact; no globs, "appropriate location," or implied destination.
- [ ] Output contract repeated in preamble, delivery step, and self-check.
- [ ] Self-check is the final instruction block.
- [ ] Destructive steps include `HARD STOP` + precondition.
- [ ] Every fork is bound in the dispatch.
- [ ] Propagation named for every touched surface that needs one (service restart, plugin install), or `propagation: none` stated.
- [ ] cursor-sdk `op=generate`: before `team_dispatch`, verify `dispatch_thread_id` has `lifecycle_state=pending ∧ turn_count=0`; otherwise halt and fix. Response `consolidation_split_warning` is too late.
- [ ] Top-level cursor-sdk generate: `lane=` set (`A` or `B`) unless `nest_under` / `resume_of`. After admit, quote the lane event / `active_by_lane`.

## Gate-2 implement-ready checklist

Before marking a `judgment_required` todo implement-ready, verify ALL:

- [ ] Pinned assertion: `todo.attributes.implement_ready_assertion_id` equals the new active assertion id.
- [ ] Predicate: `predicate_form == status({todo_id}, implement_ready, current)` after lowercase/whitespace normalization. Lead claim with implement-ready intent; avoid reopened/in_progress phrasing. Set `predicate_form` explicitly if normalizer mis-targets.
- [ ] Dense spec cited via reader-resolvable `evidence_uris`: `cortex:`, `cortex://`, `workspaces:`, `workspaces://`, `ws:`, `ws://`, or bare `cortex://notes/system/specs/{slug}.md` / `notes/system/specs/{slug}.md`.
- [ ] Content hash cited as exact `spec_sha256:<hex>` from `dense_spec_hash_uri(spec_text)`, not `sha256:<hex>`.
- [ ] Entity attrs `files_expected` and `acceptance_criteria` are populated from the dense spec, not defaults/empty.

## Materializer preconditions

`team_dispatch(op=generate, seat=cursor-sdk, contract=implement, source_ref=todo:{slug})` reads entity state, not dispatch params. Required:

1. `todo.attributes.density_triage ∈ {judgment_required, mechanical}`. `mechanical` bypasses spec; any other value rejects (`implement_triage_unknown`).
2. Todo entity `source_uri` points at `cortex://notes/system/specs/{slug}.md` or `notes/system/specs/{slug}.md`; `attributes.spec_path` is ignored.
3. Dense spec passes `validate_dense_spec`: eight ATX sections — problem; non-goal/scope exclusion; source-of-truth/provenance; touch-point; bound design/fork table/design decision/resolved fork; implementation guidance/steps; acceptance; verification/quality gate — plus non-empty `<reasoning_trace>...</reasoning_trace>` containing literal `no fork remains open`, and zero visible `OPEN:` markers. Matching is code-stripped; headings inside fences do not count.

First-failure order: `implement_triage_unknown` → `implement_not_ready_judgment_required` → `implement_ready_assertion_missing` → `implement_ready_assertion_entity_mismatch` → `implement_ready_assertion_inactive` → `implement_not_ready_no_dense_spec` → `implement_ready_assertion_spec_uncited` → `implement_spec_unreadable` → `implement_spec_not_dense` → `implement_spec_drifted_since_ready` → `implement_attrs_unpopulated`.

Dry-run before declaring ready: `team_dispatch(op=generate, seat=cursor-sdk, contract=wrap, source_ref=todo:{slug})`. `wrap` runs the same `require_implement_ready` gate then materializes a packet without SDK worker. `422` names the missing precondition; `200 + packet_path` means execute will admit.

Re-versioned / previously done todo: stale `implement_ready_assertion_id` may be reused by source-ref materialization. Supersede old assertion, re-pin, re-distill attrs, or use `packet_path` until verified.

## Contract coverage

| Contract | Instruction density | Self-check placement |
|---|---|---|
| `pure-mechanical` | Exact symbol + location + value | Inline final block |
| `light-bounded` | Step-by-step + acceptance criteria inline | Inline final block |
| `implement` | Materialized from todo attrs/server packet | `acceptance_criteria` at Gate-2 |

## Failure anchor

Friction 19196: a `light-bounded` dispatch wrote to `tmp/reviews/` instead of named cortex sidecar, while summary omitted bound assertion 19188. Root cause: no explicit delivery path, no self-check, output contract stated once. Treat worker self-report as advisory; lead verification remains required for canonical/destructive writes.

## Charter-window terminal (thin — landed 5712)

Autonomous charter windows must post a window terminal (`CHECKPOINT` /
`CONSULT_PENDING` / `BLOCKED` / `PACKAGING_DEFICIT`) before exit. Missing terminal
after worker `complete`/`partial` ⇒ `checkpoint_missing`, and **nothing repairs it**
— Phase 3 retired the tick's self-heal, so the window is lost and the root's pickup
stalls until an author reseeds the tip. Digest: Use the `agent-bus-discipline` skill
§ Autonomous tick runtime.

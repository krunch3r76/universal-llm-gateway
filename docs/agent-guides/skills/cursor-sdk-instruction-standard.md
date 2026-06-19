---
name: cursor-sdk-instruction-standard
description: Load before authoring any cursor-sdk dispatch turn — light-bounded, pure-mechanical, or implement contract. Ensures Composer 2.5 (low-tier mechanical executor) receives instructions dense enough to satisfy output contracts without judgment gaps or missed delivery. Triggered by: writing a cursor-sdk dispatch, authoring light-bounded instructions, embedding self-check, output contract drift, lesson_gap friction.
skill_category: dispatch-delegation
trigger_short: cursor-sdk ∨ light-bounded ∨ pure-mechanical ∨ self-check ∨ acceptance_criteria
trigger_match_terms: ["cursor-sdk", "cursor_sdk", "light-bounded", "pure-mechanical", "acceptance_criteria", "self-check", "instruction standard", "team_dispatch", "contract=implement"]
canonical: workspaces://universal-llm-gateway/docs/agent-guides/skills/cursor-sdk-instruction-standard.md
---

# Cursor-SDK Instruction Standard

Composer 2.5 is a low-tier mechanical executor. It optimises for appearing done.
Default: explicit, determinate, repeating instructions + mandatory self-verification.

Every cursor-sdk dispatch turn — regardless of contract — must satisfy four disciplines.

**Light execution:** `op=generate`, `role=cursor-sdk` invokes **Composer without IDE handoff**
(no `cursor-implement` pickup). Prefer **`contract=implement`** when implement-ready;
narrower contracts when appropriate. Load this skill before authoring the dispatch turn.
On `op=generate`, a `subject` arg is accepted but **ignored** (result-thread subject is
auto-derived; response carries a `subject_ignored_on_generate` warning) — use `op=to_thread`
to set a thread subject (friction 19803).

## D1 — Determinate steps

∀ instruction: name the file, the symbol, the exact value. No open-ended directives.
∀ fork / design choice: bind it in the dispatch — never leave judgment to the worker.

| Bad | Good |
|---|---|
| "Improve error handling in route.py" | "In `route.py::handle_admit`, replace `except Exception` with `except AdmitError as exc`; log via `logger.warning(exc)`." |
| "Write the result to an appropriate location" | "Write the sidecar to `cortex://notes/system/threads/{thread_id}-review.md` AND assert the sidecar URI on `todo:{slug}` as `evidence_uris`." |

## D2 — Deliberate constraint repetition

State the output contract in the preamble. Repeat it verbatim at the delivery step.

Anti-pattern: one mention in an opening paragraph → drifts out of Composer attention mid-turn.
Required: constraint appears in preamble **and** at the fulfillment step **and** in the self-check.

## D3 — Mandatory self-check clause

The final instruction block MUST be an explicit self-check the worker executes and reports inline.

```
SELF-CHECK — mandatory before reporting done:
1. [Primary output]: confirm [artifact] exists at [exact path / cortex key].
2. [Secondary constraint]: confirm [observable state, e.g. sidecar bound in evidence_uris].
Report each: PASS / FAIL + one-line evidence.
Reporting "done" without a passing self-check is a contract violation.
```

- `light-bounded` / `pure-mechanical`: embed inline as the final instruction block.
- `contract=implement`: embed in `acceptance_criteria` at Gate-2 distillation time.

## D4 — Preflight hard-stop on destructive ops

∀ step that overwrites, deletes, or supersedes a durable artifact:

```
HARD STOP: verify [precondition] before proceeding. If not met, halt and report.
```

Irreversible ops (cortex supersede, file overwrite, entity delete) require an explicit
precondition assertion in the instruction — never left to implicit assumption.

## Pre-dispatch checklist

- [ ] **Verify live** (implement contract) — `implement-todo` §1: `entity_get(todo:{slug})`, confirm `workflow_state ∈ {open, in_progress}` (not `done`/`superseded`/stale; the entity is canonical, boot-card/bus rows go stale)
- [ ] All file / cortex paths named explicitly — no glob-implied, no "appropriate location"
- [ ] Output contract stated in preamble AND repeated at the delivery step
- [ ] Self-check clause present as the final instruction block (D3 template above)
- [ ] Any destructive step prefixed with HARD STOP + precondition
- [ ] ∀ fork: bound in the dispatch (nothing left to worker judgment)

- [ ] **Thread consolidation gate** (cursor-sdk `op=generate` only) — before firing `team_dispatch`, verify `dispatch_thread_id` is `lifecycle_state=pending` AND `turn_count=0`. If either fails, halt and correct. See `orchestrator-workflow.md` § Pre-dispatch preflight (mandatory gate) for the probe call + fail actions. The `consolidation_split_warning` in the dispatch response is too late.\n

## Gate-2 implement-ready checklist (distillation close)

Before recording implement-ready on a `judgment_required` todo, verify ALL of:

- [ ] **Pinned assertion id** — `todo.attributes.implement_ready_assertion_id` is set to the new assertion row id after the `assert`/`supersede` lands (the materializer gate reads this pin, not a stale id).
- [ ] **Predicate form** — `predicate_form` normalizes (whitespace-stripped, lowercased) to exactly `status({todo_id}, implement_ready, current)`. Lead the **claim** with implement-ready intent; avoid "reopened (in_progress)" / "in_progress" phrasing — it normalizes to an `in_progress` predicate and the readiness gate ignores it. Set `predicate_form` explicitly on the `assert`/`supersede` if the normalizer mis-targets.
- [ ] **Dense spec citation** — `evidence_uris` cites the dense spec via a reader-resolvable scheme (`cortex:…`, `cortex://…`, `workspaces:…`, `workspaces://…`, `ws:…`, `ws://…`, or bare path under `tasks/specs/{slug}.md` / `notes/system/specs/{slug}.md`).
- [ ] **Content hash token** — `evidence_uris` includes the exact `spec_sha256:<hex>` token from `dense_spec_hash_uri(spec_text)` (NOT `sha256:<hex>`). The gate rejects drift with `implement_spec_drifted_since_ready` when only `sha256:` is cited.
- [ ] **Attrs distilled** — `files_expected` and `acceptance_criteria` are populated from the dense spec (not defaults/empty).

**Re-versioned / previously-done todo:** a stale pinned `implement_ready_assertion_id` can be silently reused by `source_ref` materialization — supersede the old assertion + re-pin + re-distill attrs, or prefer `packet_path` until verified. Full lane: cortex `consult-routing.md` § Implement lane / Densify lane.

## Contract coverage

| Contract | Instruction density | Self-check placement |
|---|---|---|
| `pure-mechanical` | Exact symbol + location + value | Inline final block |
| `light-bounded` | Step-by-step + ACs inline | Inline final block |
| `implement` | Materialised from todo attrs (server) | Embed in `acceptance_criteria` at Gate-2 |

## Incident grounding (friction 19196)

A `light-bounded` Phase-1 dispatch (exec `655f7d9a`) wrote the deliverable to
`tmp/reviews/` instead of the named cortex sidecar, and its machine-summary reported
`cortex_assertions:[]` despite having bound assertion 19188. Root cause: no explicit
delivery location in the instruction, no self-check clause, constraint stated once in
preamble only. Self-reported "complete" required lead-seat verification to trust.

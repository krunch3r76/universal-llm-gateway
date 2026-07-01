---
name: cursor-sdk-instruction-standard
description: Load before authoring any cursor-sdk dispatch turn — light-bounded, pure-mechanical, or implement contract. Ensures Composer 2.5 (low-tier mechanical executor) receives instructions dense enough to satisfy output contracts without judgment gaps or missed delivery. Triggered by: writing a cursor-sdk dispatch, authoring light-bounded instructions, embedding self-check, output contract drift, lesson_gap friction.
skill_category: dispatch-delegation
trigger_short: cursor-sdk ∨ light-bounded ∨ pure-mechanical ∨ self-check ∨ acceptance_criteria
trigger_match_terms: ["cursor-sdk", "cursor_sdk", "light-bounded", "pure-mechanical", "acceptance_criteria", "self-check", "instruction standard", "team_dispatch", "contract=implement"]
canonical: workspaces://universal-llm-gateway/docs/agent-guides/skills/cursor-sdk-instruction-standard.md
---

# Cursor-SDK Instruction Standard

Composer 2.5 is a capable mechanical executor; its self-reports have generally been reliable in practice.
Verification is retained as cheap defense-in-depth — the lead's independent check is the verification of record
for canonical/destructive (irreversible) writes, not a presumption that the worker misreports.
Default: explicit, determinate, repeating instructions + a worker self-check, with lead-seat verification on irreversible writes.

Every cursor-sdk dispatch turn — regardless of contract — must satisfy five disciplines (D0–D4).

**Light execution:** `op=generate`, `role=cursor-sdk` invokes **Composer without IDE handoff**
(no `cursor-implement` pickup). Prefer **`contract=implement`** when implement-ready;
narrower contracts when appropriate. Load this skill before authoring the dispatch turn.
On `op=generate`, a `subject` arg is accepted but **ignored** (result-thread subject is
auto-derived; response carries a `subject_ignored_on_generate` warning) — use `op=to_thread`
to set a thread subject (friction 19803).

## D0 — Default `<mcp_capabilities>` block (durable-output routing)

**Operator directive (Kaywan, 2026-06-28): an explicit `<mcp_capabilities>` block is the DEFAULT on _any_ `team_dispatch` to a cursor-sdk model** — every contract (`light-bounded`, `pure-mechanical`, `implement`, `wrap`), every task class (recon, review, implement), not just consult packets.

**Why it is the default, not an option.** cursor-sdk (Composer) writes deliverables into the **project checkout `tmp/`** by default — the SDK wrapper always drops a closeout receipt at `tmp/reviews/closeouts/<dispatch_id>.md`, and absent explicit routing the worker tends to co-locate its real output there too. Without explicit cortex routing, Composer also gravitates to **`/tmp/summaries/`** (from the always-applied `system.mdc` Locations table). Durable output reaches Cortex **only** when the packet names the cortex sandbox write path explicitly. Omitting the block is the root cause of the friction-19196 class (deliverable written to `tmp/reviews/` instead of the named cortex sidecar).

The block MUST:

- Enumerate the MCP tools the worker should use (`rag`, `fs`, `cortex`, …) with the `arguments`-shape reminder — **JSON string** for `rag` / `cortex` / `agent_bus` / `dispatch`; **typed top-level object** for `fs`.
- Name the **exact sandbox + path** for every durable write — `fs(sandbox="cortex", op="write", path="notes/system/threads/<file>.md")` — never "an appropriate location." (Cortex paths are relative to `/data/files`, no leading slash.)
- Instruct the worker to cite each tool call inline.

```
<mcp_capabilities>
You have MCP tools. Use them; do not limit yourself to this packet.
0. rag(op="search", arguments='{"query":"...","scope":"..."}')  — arguments is a JSON STRING.
1. fs(sandbox="cortex", op="write", path="notes/system/threads/<file>.md", content="...")  — durable deliverable lands HERE, not in tmp/.
Cite each tool call inline.
</mcp_capabilities>
```

Validated 2026-06-29: a `light-bounded` recon (dispatch `8f54761d…`, thread 3422) with an explicit cortex-sandbox `<mcp_capabilities>` block wrote all five deliverables to `cortex:notes/system/threads/` correctly; only the automatic SDK closeout receipt landed in project `tmp/`.

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

- [ ] **`<mcp_capabilities>` block present (D0)** — every cursor-sdk dispatch; names exact cortex sandbox + path for each durable write (deliverables go to Cortex, not project `tmp/`)
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

### Materializer preconditions (entity-level — easy to miss; each is a hard 422)

The boxes above are necessary but NOT sufficient. `team_dispatch(op=generate, role=cursor-sdk, contract=implement, source_ref=todo:{slug})` reads THREE further preconditions from the **entity** (not the dispatch params). A stamp that satisfies every box above still hard-fails at execute if any of these are unset, forcing the lead to reverse-engineer the gate from 422 codes (incident: friction 20651, hit sequentially on 20588). SOT: `libs/implement_admission/implement_ready.py::evaluate_implement_ready` + `dense_spec_schema.py::validate_dense_spec`.

- [ ] **Density triage** — `todo.attributes.density_triage` is `judgment_required` (runs the full gate) — or `mechanical` to bypass the spec entirely and admit immediately. NO other value admits: `cross_cutting` / `dispatch_surface` / `admission_path` / `trivial` (the `team_dispatch` `density_triage` param enum) all reject with `implement_triage_unknown`. The gate reads the **entity attribute**, never the dispatch param.
- [ ] **Entity-level `source_uri`** — the todo **entity** `source_uri` (NOT `attributes.spec_path`, which this gate never reads) points at `tasks/specs/{slug}.md` or `notes/system/specs/{slug}.md`. Empty → `implement_not_ready_no_dense_spec`.
- [ ] **Dense-spec section schema** — the spec file passes `validate_dense_spec`: EIGHT ATX-headed sections must be present — `problem`; `non-goal` / `scope exclusion`; `source-of-truth` / `provenance`; `touch-point`; `bound design` / `fork table` / `design decision` / `resolved fork`; `implementation guidance` / `implementation steps`; `acceptance`; `verification` / `quality gate` — PLUS a non-empty `<reasoning_trace>…</reasoning_trace>` block whose body contains the literal attestation `no fork remains open`, AND **zero `OPEN:` markers** in the visible text. Matching runs on **code-stripped** text (fenced + inline code removed), so headings inside ``` fences do not count. Missing sections → `implement_spec_not_dense` (`dense_spec_sections_missing`); a leftover `OPEN:` → `dense_spec_open_forks`.

**Gate order (first failure wins):** `implement_triage_unknown` → `implement_not_ready_judgment_required` → `implement_ready_assertion_missing` → `implement_ready_assertion_entity_mismatch` → `implement_ready_assertion_inactive` → `implement_not_ready_no_dense_spec` → `implement_ready_assertion_spec_uncited` → `implement_spec_unreadable` → `implement_spec_not_dense` → `implement_spec_drifted_since_ready` → `implement_attrs_unpopulated`.

**In-session dry-run — the densify session catches its own gap.** Densification runs in a *separate* web-claude consult session that the server gate cannot reach until execute, so this checklist is the web-side complement to the server admission gate (`todo:densification-implement-admission-gate`, done — `require_implement_ready`). To self-validate before declaring ready, fire `team_dispatch(op=generate, role=cursor-sdk, contract=wrap, source_ref=todo:{slug})`: `wrap` runs the identical `require_implement_ready` gate **then** materializes the packet with NO SDK worker (gate-then-materialize, `generate_wrap.py`). A 422 surfaces the failing precondition in-session; HTTP 200 + `packet_path` means execute will admit. (Inspection-only use of `wrap`, per consult-routing § Densify lane.)

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
preamble only. Self-reported "complete" is treated as advisory: this was a rare divergence with a likely-benign tooling cause (cf. assertion 20188), not evidence that Composer routinely misreports — and it is why lead-seat verification is retained on canonical/destructive writes as defense-in-depth.

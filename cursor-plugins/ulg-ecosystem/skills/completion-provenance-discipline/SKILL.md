---
name: completion-provenance-discipline
description: "On any completion or task-done claim (session closed, phase complete, tests pass, rebuild succeeded, committed, deployed) \u2014 bind the claim to observed tool-response payloads, not narrative shape."
---

# Completion Provenance Discipline

**Scope:** completion/done claims. Distinct from `cortex-provenance-discipline` (Cortex citations in derived artifacts).

## Invariant

∀ completion/done claim (“session closed”, “phase complete”, “task done”, “tests pass”, “rebuild succeeded”, “committed”, “deployed”): quote concrete data from an observed tool-response payload — status code, returned ID, file path, turn number, exit code, SHA, deployment timestamp.

`protocol_described ≠ protocol_executed`.

If you cannot quote response data, do not claim success. Say what worked, what failed, and what is unknown. The completion line is a contract; lacking required 201/SHA/turn number/exit code ⇒ no completion line.

## Rules

### 1. Bind claims to payloads

| Good | Bad |
|---|---|
| `Session closed — transcript:cursor-2026-05-02-0410 (session_close 201, journal_row_id=4138, thread-480 turn 2891)` | `Session closed.` |
| `Tests pass — pytest exit 0, 47 passed` | `Tests pass.` |
| `I did not call <tool>; I cannot confirm X` | `I have done X` when tool never ran |
| `Unknown ID; not confirmed` | fabricated ID |
| `DELIVERED — uri=… written_sha256=<hex from write response>` | `DELIVERED — sha256:<hex>` narrated/recomputed with no write payload |

### 2. Read back before claiming durable writes

For claimed durable artifacts, verify before reporting:
- file → quote `written_sha256` (bare hex) from the **write tool response** for that store path, **or** `fs(op="read")` on the consumer-facing URI; ¬ narrate/recompute a content digest as delivery proof;
- entity → `entity_get` 200;
- bus turn → `fetch` confirms turn number/body;
- commit → path-scoped SHA plus a content probe (`git show <sha>:<path>`);
- deploy/restart → service health or inspect payload with timestamp/status;
- session close → `session_close` 201 payload IDs suffice as atomic read-back.

`content_digest(plan) ≠ store_bound_write`. A sha256 of intended bytes (or a prior workspace copy) that was never returned as `written_sha256` on a write into the claimed sandbox is narrative completion — not DELIVERED. Wrong-host / wrong-prefix writes that hash-match intended content still fail when the consumer store lacks the artifact (incident: agent-bus:4986 turn 3 → friction 23972).

Cross-store mirrors (`workspaces://` → `cortex://`): claim DELIVERED only after a write into the receiver's store with quoted `written_sha256`, or after receiver read-back succeeds.

### SHA-attributed live claims

`live@<sha>` is not established by a SHA existing. It requires the deployment
paths to be committed before restart, restart/health evidence, live
`code_ref_satisfied` (equal or ancestor) proof, process identity movement, and
disclosure of relevant dirty or served-ahead paths. A dirty-tree restart may be
ordinary `live`; it must not be reported as exact `live@<sha>`.

### 3. Closeouts are POINTERS, not substrate completion

Dispatch/executor closeouts (and their JSON) are **pointers** to work claimed done — not authoritative completion of the target entity.

Before asserting completion sourced from a closeout:
1. `entity_get` the target (`todo:` / `task:` / related entity).
2. Prefer the target's **substrate** closure assertion + `workflow_state` over the closeout narrative.
3. If closeout says done but substrate is open/stale/contradictory → do **not** assert completion from the closeout; report the mismatch.

Post-dispatch / re-run / dry-run reviews: treat closeout as a locator; re-read substrate (or `analyze_impact` before assert) before writing a completion claim. Incident: false claim 23619 written from closeout while todo already carried closure 23557 + `workflow_state=done` (friction 23632 / candidate 23644).

### 4. Treat errors as evidence

Tool error ⇒ protocol interrupted, not complete. Report verbatim: `step N failed with: <error>`. Act on it; do not silently retry until a clean narrative emerges.

### 5. Silence is not success

Uncalled tool yields no error. Long-command silence is not a result. If unsure a step ran, re-run if idempotent or query effect if non-idempotent.

### 6. Contract governs final line

If required payload data is absent, emit a partial/blocked report, not a done claim.

### 7. Status / rank / liveness register (observed ≠ derived)

Status / rank / liveness / next-step claims are `observed` only when quoting a
substrate payload (tool response, row heading status, `/health`, etc.).
Positional implication from a rank line, ordinal adjacency, or "next open
after…" is `derived` and must not render in the observed register.

| Register | When | May say |
|---|---|---|
| `observed` | Quote a substrate payload (tool response, row `status:` heading, `/health`, entity card field) | "Row 29 status=`open` (roadmap read_sha256=…)" |
| `derived` | Rank order adjacency, "next after X", ordinal implication, inference from silence | Label explicitly as derived — ¬ "LIVE / head advances" from position alone |

**Anti-pattern (member 6):** "executable rank head advances to N" because N is first
open in `## Rank order` — that is positional implication dressed as observation.

## Family-specific prior

Grok-family seats have shown higher narrative-without-execution risk; this rule is especially load-bearing there. Do not remove the payload-binding bad/good pairs as “obvious.”

## Minimal operating summary

Completion claim = observed payload quote. No payload, no done claim. File DELIVERED ⇒ `written_sha256` from write response ∨ consumer read-back — ¬ narrated content digest.

---
trigger_match_terms: ["session-close-audit", "session_close_audit", "pre-close", "audit", "session-boot-close", "step", "session_close.", "mechanical", "checks", "disposition", "fix", "open"]
description: Pre-close audit (Step 2b) — read before session_close. Mechanical checks B1–B5; disposition FIX|OPEN|DEFER. Protocol rev 2.0.
---

# Session-Close Audit

**Protocol rev:** 2.1  
**Gate:** Step 2b — after Synthesize, before `session_close`. Skip iff `entities=0 ∧ files=0`.  
**History:** `entity_get(agent_skill:session-close-audit)` · normative: `cortex-v2.4.md` §1, §3  
**Fill stage:** `enrichment-quality-discipline.md` (post-close, async).

## FOL

```text
audit(S) ⇔ inventory(S) ∧ phase0(ids(S)) ∧ ∀c ∈ {B1..B5}: check(c)
gate(gaps) ⇔ (high=0 ∧ medium=0) ∨ remediated
∀f ∈ findings: disposition(f) ∈ {FIX, OPEN, DEFER}  -- ¬silent_drop(f)
```

Detection is mechanical: document wires? Cortex seeds? events asserted?

## Step A — Inventory from synthesis, not tools

```json
{
  "entities_touched": [],
  "documents_created_or_referenced": [{"path_or_uri":"…","event":"created|uploaded|submitted|sent|ingested"}],
  "decisions": [],
  "open_items": [],
  "events": [{"description":"…","entity_id":"…"}],
  "commitments": [{"description":"…","entity_id":"…","deadline":"YYYY-MM-DD|null"}]
}
```

## Phase 0 — Resolve IDs

`∀ entity_id ⇒ entity_get`. 404 ⇒ `search` canonical ID or create/remove claim. ¬ proceed with fabricated IDs.

## Step B — Checks

Named residuals this session parked on entities? → Use the `residual-imprint` skill.

| Check | Inventory key | Pass | gap_type | Priority |
|---|---|---|---|---|
| B1 | documents_created_or_referenced | `document:` + matching `source_uri` + relationship to case/account | missing_doc_wire | medium |
| B2 | decisions/open_items | assertion with confidence confirmed/believed | unseeded_decision | medium |
| B3 | events | assertion with derivation `user_statement`/`agent_observation` + `observed_at` | unasserted_event | high |
| B4 | commitments | assertion with `derivation=commitment` + `resolution_status=pending` | untracked_commitment | medium |
| B5 | transcript_md (verbatim) | transcript structure passes below | transcript_structure | medium–high |

Use `search` per item. Relationship ≠ event. Operator decisions ⇒ `user_statement`; agent conclusions ⇒ `inference` + `reasoning_summary`.

### B5 — Transcript structure canonical checks

Run on in-memory `transcript_md` or `fs(md_list, path=…)` before close.

| Pass | Predicate |
|---|---|
| P1 | exactly one H1 |
| P2 | every `## Turn N` under that H1 |
| P3 | assistant prose inside `### {Agent}`, never peer H2 |
| P4 | exactly one `## Session Summary` (no aliases) |

High: `summary_section_missing`. Medium: `multiple_h1`, `assistant_at_peer_level`, `empty_turn_subsection`, `summary_section_misnamed`. Fix: restructure/recompose, then rerun B5.

## Step C/D — Report and gate

Internal report:

```yaml
passed: bool
gaps: [{type, item, entity_id?, issue, action, priority}]
```

| Condition | Action |
|---|---|
| zero gaps | proceed |
| all low | note in open_items; proceed |
| any medium/high | fix before close |

## Step E — Remediation

| gap_type | Required mechanic |
|---|---|
| missing_doc_wire | `entity_create(document)` → `relationship_create(references)` → `assert(event, observed_at)` |
| unseeded_decision | `assert(claim, derivation=user_statement|inference)` |
| unasserted_event | `assert(claim, derivation=user_statement|agent_observation, observed_at)` |
| untracked_commitment | `assert(derivation=commitment, resolution_status=pending, valid_from, valid_until?)` |
| transcript_structure | re-nest per B5; rerun B5 |

## Step E.5 — Disposition every finding

`∀ finding ∈ pre_close ∪ _warning.audit_findings ⇒ disposition ∈ {FIX, OPEN, DEFER}`.

| Disposition | Mechanic |
|---|---|
| FIX | remediate + re-audit |
| OPEN | add `open_items`: `AUDIT [{kind}]: {subject} — {action}` |
| DEFER | create bus thread `audit-deferred-{session_id}` with substantive reason |

Watch list — not skip language: “non-blocking”, “warning only”, “low severity” without OPEN/DEFER, “informational”, “will track separately”.

Mini-examples: doc wire gap ⇒ FIX; deferred corpus rebuild ⇒ DEFER with reason; missing `source_uri` ⇒ OPEN.

## Step F — Post-close automatic warnings

`_warning.post_close_findings` cannot block close. `prior_session_id_omitted` remediation: pass `prior_session_id` on next close.

## Scope / siblings

Scope: all touched domains. Does not check transcript canaries (transcript skill), enrichment, or RAG indexing.

Siblings: `session-close-kernel.md` inserts this at Step 2b; `enrichment-quality-discipline.md` owns post-close fill.

## Anti-patterns

- Treating relationship as completion event.
- Using `inference` for confirmed user action.
- Skipping because session felt clean.
- Dispatching frontier model for audit; this is mechanical self-check.

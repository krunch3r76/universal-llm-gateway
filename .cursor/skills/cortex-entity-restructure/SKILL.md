---
trigger_match_terms: ["cortex-entity-restructure", "cortex_entity_restructure", "entity", "restructure", "split", "migration", "cortex-planning", "cortex", "graph", "cleanup", "assertion", "supersession"]
description: On any Cortex entity restructure, graph cleanup, entity split, assertion migration, supersession pass, or child node creation — read this skill before planning or executing.
---

# Cortex Entity Restructure

**Version:** 1.2 · **Updated:** 2026-05-03 · **Authority:** HIGH for Cortex graph hygiene/entity splits.  
Companions: `advisor-timing` (read-only plan before writes), `document-lifecycle-tracking` (document children), `financial-reasoning` (financial entities).

## Trigger

Read before generating/reviewing a restructure plan, executing an approved restructure, splitting an overloaded parent, migrating assertions to event/note/decision children, bulk-superseding parent assertions, or graph cleanup touching `>3` entities or `>10` assertions.

`approved_restructure_plan ⇒ Phase 2`; read before execution.

## Two-phase contract

### Phase 1 — read-only plan

Before mutations, post a written plan and wait for explicit approval. Silence/prior context ≠ approval.

Plan fields:
- child entity IDs, types, names, descriptions;
- source assertion IDs → destination child;
- assertions staying on parent;
- correction flags (claim changes on migration);
- execution order.

### Phase 2 — execute approved plan

Do not deviate without surfacing delta + re-seeking approval. Confirm every plan field before mutating.

## Core principle

Parents hold live operative state. Children hold topical history.

Typical split:
- `event:*` — timelines, blockers, milestones, communications history;
- `note:*` — economics, contract interpretation, underwriting notes, open items;
- `decision:*` — provider comparison, corrected verdicts, chosen path;
- `document:*` — artifacts wired via `references`;
- parent account/case — current operative assertions + summary state only.

Child ID convention: `{type}:{parent-slug}-{topic}` (e.g. `note:hometap-investment-economics`).

## Phase-2 preflight

1. Read approved plan; confirm child IDs/names/descriptions, source assertion migrations, parent-retain assertions, correction flags, execution order.
2. Pull parent assertions: `cortex(tool="assertions", arguments='{"entity_id":"...","limit":100}')`.
3. Confirm relationship types before first write: `cortex(tool="edge_types")`. Missing `related_to`/`references`/custom types can fail mid-pass and leave partial state.
4. Pull referenced case/property/document entities with `entity_get(..., include_edges=true, edge_limit=20)`.
5. Build migration table:
   - **Bucket 1 — migrate + supersede:** child now represents content; parent gets archive pointer.
   - **Bucket 2 — promote staged:** staged parent assertion should be committed, not relocated.
   - **Bucket 3 — in-session duplicate:** duplicate parent/child content; supersede parent, keep child canonical.
   - **Bucket 4 — retain:** current operative state stays on parent.
6. Already-superseded source assertion ⇒ do not add second supersession unless operator explicitly directs.

## Canonical execution order

1. **Create all approved children** before child assertions:
   `entity_create({id,type,name,description})`.
2. **Seed atomic child assertions.** Re-read relevant parent assertions, compress into clean atomic claims, write to child.
   - Do not paste compound parent assertions verbatim.
   - Preserve substance and source confidence (`confirmed`→`confirmed`, `believed`→`believed`).
   - Multiple parents supporting one child claim ⇒ cite them in `evidence`.
   - Correction flag ⇒ write corrected child claim.
   - Wrong-but-important historical assertion ⇒ represent as “initial interpretation was X” + “later correction established Y”.
   - Inference assertions: include `reasoning_summary` to avoid staging.
   - Claim contains date reference (YYYY-MM-DD, ISO timestamp, named date) ⇒ `valid_from` required; 422 body includes derivation hints.
3. **Wire structural relationships** exactly as approved, commonly:
   - `parent --[related_to]--> child`
   - `case --[related_to]--> decision`
   - `document --[references]--> note`
   Use `relationship_create({source_id,target_id,type_id,strength,evidence,session_id,agent})`.
4. **Wire document references** with `type_id:"references"` when a document is operative evidence (appraisal, PDF, deed, contract, etc.).
5. **Supersede migrated live parent assertions** with one-line archive pointers:
   - `Archived to child:id — closing-gate history.`
   - `Archived to child:id — economics note.`
   - `Archived to child:id — underwriting milestone.`
   Skip already-superseded rows unless explicitly directed.
6. **Add clean parent current-state summary**: short, live, date-anchored, latest-state-corrected. If contradiction detection triggers: narrow claim → remove extra detail → retry → if still blocked, read conflicts and supersede stale row before retry. `force` is last resort only after operator acknowledges conflict and directs override.
7. **Verify** with `entity_get(..., include_edges=true, edge_limit=10)` on every child and touched parent. Confirm child assertions, relationships, archive pointers, originals `superseded_by`, parent current-state summaries, and no unexpected staging. Phrase sanity checks as deltas against explicit prior lists (e.g. “promoted exactly 8078, 8079…”), not end-state claims (“active staged list empty”).
8. **Promote staged assertions only when explicitly staged/flagged.** If `review_status:"staged"`, run `assertion_update(..., review_status:"committed", reviewer, review_notes)` then re-verify. `assert` success returning `review_status:null` is post-validation success, not uncommitted; do not auto-promote null rows. `supersede` writes explicit `committed`, so mixed null/committed rows are harmless.

## Tool-surface gotchas

- Cortex `arguments` is always a JSON string: `arguments='{"entity_id":"..."}'`, never a bare object.
- Date-pattern claim ⇒ `valid_from` required.
- Provenance asymmetry: `relationship_create`/`supersede` accept `session_id` + `agent`; `assert` uses `seeded_by`, evidence text, `valid_from`, `reasoning_summary`.
- `edge_types` before relationship writes; prefer confirmed `references` or `related_to`.
- Read 409 contradiction responses; do not brute-force with `force`.

## Reporting template

Return:
1. entities created;
2. child assertions seeded (count + IDs);
3. relationships wired (count + IDs);
4. parent assertions superseded (old→new mapping);
5. current-state assertions added;
6. caveats/deferred items: staged/promoted rows, contradiction friction, already-superseded skipped, plan deviations;
7. skill file draft if requested.

## Anti-patterns

Do not:
- execute without approved Phase-1 plan;
- leave live status buried only in historical children;
- copy huge compound assertions verbatim;
- supersede already-superseded assertions by habit;
- forget document evidence links when document entity exists;
- migrate assertions marked “stay on parent”;
- treat missing child assertion as no migration without `entity_get` verification;
- ignore 409 conflicts or use `force` before reading;
- omit `valid_from` on dated claims;
- skip staged/null review-status distinction;
- use binary “in child or not” classification instead of 4 buckets.

## Success condition

A restructure succeeds when parent reads as live status node; history is preserved on topical children; migrated parent assertions are superseded by archive pointers; document/structural links are explicit; child assertions are committed or valid null-success (not staged); and top-down graph read has no semantic ambiguity.

## Pointer compaction follow-on

Standard restructure leaves N parent archive-pointer assertions. Pointer compaction collapses pointer rows into one consolidation summary while relationship edges preserve navigation.

Frame honestly: compaction is slim-card prep, not immediate naïve `entity_get` byte reduction. Old pointers persist with `superseded_by`, and new summary + supersede rows add bytes. Payload win arrives only when renderers treat compacted parents as slim-card candidates.

### When to compact

Use only when pointer count is large enough (≈10+) and children/promotional state are clean. Do NOT compact with active parent conflict or mostly staged children.

### Compaction procedure

1. Inventory pointer assertions (usually claims beginning “Archived to <child_entity_id> — …”).
2. Verify every pointer has backing parent→child relationship edge. Missing edge ⇒ STOP, wire relationship first.
3. Write one compacted summary assertion naming all child entity IDs.
4. Supersede each pointer to the new summary assertion ID.
5. Verify with delta-claim discipline.

### Summary schema gate

For session-synthesized compaction summaries use `derivation_type:"inference"` + non-empty `reasoning_summary`; dated claims require `valid_from`. Do NOT use `derivation_type:"compression"` unless backed by ingested-document `chunk_id` + non-empty `evidence_uris`. For reserved vocab (`derivation_type`, `edge_type`, `confidence`, `review_status`, statuses), consult data dictionary; spec prose ≠ schema permission.

### Supersede caveat

Short pointer claims may return non-blocking `impact_warning: "Assertion <id> not found in semantic impact analysis..."`. Log and continue.

### Compaction success

All pointer rows point via `superseded_by` to summary; one summary exists (committed or null-success); N supersede-output rows point to same summary; Bucket-4 retain rows unchanged; parent→child relationship-edge connectivity intact.

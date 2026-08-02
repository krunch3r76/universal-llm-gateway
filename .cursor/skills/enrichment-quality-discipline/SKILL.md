---
trigger_match_terms: ["enrichment-quality-discipline", "enrichment_quality_discipline", "post-close", "sparse-entity", "enrichment", "session-boot-close", "step", "session-close.md", "v3.3", "skill", "emitting"]
description: 'On post-close sparse-entity enrichment (Step 6 of session-close): read before emitting any enrichment assertion — gates on transcript_depth=verbatim first, then seat policy.'
---

# Enrichment Quality Discipline

**Version:** 2.2-compressed  
**Authority:** sole canonical skill for post-close enrichment after successful `session_close`. `session-close` Step 6 is pointer-only; do not inline this discipline there.

## Scope

Post-close enrichment is advisory reasoning work after `session_close` 201: transcript-grounded assertions/edges, confidence calibration, sparse-entity relation repair. It complements pre-close `session-close-audit` (synchronous/gating detection).

`pre_close_audit = gating`; `post_close_enrichment = advisory`.

## First gate: transcript depth

`enrich?(S) ⇒ transcript_depth(S) = "verbatim"`.

Check this before seat policy, sparseness, dispatch, or the four disciplines. `light` and `none` are not enrichment-eligible.

Kernel rev 4.1 note: the default close depth is now `light` — this gate therefore short-circuits on most closes. Enrichment runs only after an explicitly ceremonious/verbatim close; that is intended, not a regression.

Why:
- ratified-form attribution needs verbatim turns;
- peer-evidence assertions still need session/turn anchor;
- confidence calibration reads transcript warnings absent from light/none;
- document-existence assertions need turn-grounded `evidence_uris`.

If `transcript_depth ≠ verbatim`, stop and log only:

```python
logger.info("session_close: enrichment skipped — transcript_depth=%s (only 'verbatim' is eligible)", transcript_depth)
```

No event, friction, MCP call, or wire-visible warning.

Canonical failure: enrichment on `light` transcript cites structural summary sections as if they were verbatim turns. URI syntax can look valid while evidence is hollow. `none` has no transcript entity/file to cite.

## Seat gate

`enrich?(S) ⇔ transcript_depth(S)="verbatim" ∧ (seat=web ∨ (seat=cursor ∧ cortex_brief_cursor(S)))`.

| Seat | Enrich when verbatim? |
|---|---|
| web | yes |
| cursor | only if `cortex_brief(agent="cursor")` ran this session |

## Edge-first dispatch orchestration

Runs only after both gates pass. Prioritize relations over new assertions; failure is advisory because close already succeeded.

Sparseness gate:

```text
∀ e ∈ session_close.entity_ids:
  entity_get(e, include_edges=true) ∧ edge_count(e)=0 ∧ ¬exempt(e) ⇒ enrich(e)
```

Exempt: open `todo:`.

Pass shape:
1. Detection: filter sparse entities.
2. Edge extraction: verbatim transcript-grounded co-occurrence/semantic relationships → `relationship_create` / `edge_create`; reify mentions of 2+ entity IDs as explicit edges.
3. Triage only: flag near-duplicate assertions `review_status="staged"`; do **not** supersede.
4. Summary: bus thread `entity-enrichment-{date}` with per-entity actions.

Dispatch target:

```python
pipeline(op="run", pipeline_id="entity-enrichment", arguments={
  "entity_ids": [...],
  "transcript_path": "<from session_close 201>",
  "calling_agent": "<agent>"
}, options={"async": true})
```

Stopgap until pipeline lands: async `team_generate` to orion with inline edge-first task; capture `execution_id`; do not wait.

## Core disciplines

### 1. Ratified-form attribution

For multi-turn topics, cite the **ratified form**, not first occurrence.

Procedure:
1. Locate first turn mentioning topic.
2. Walk forward through later mentions; track latest ratified form.
3. Cross-check `## Session Summary` decision list.
4. Cite ratified turn as `transcript:<sid>#turn-N`.
5. If ratified form differs materially from first occurrence, note evolution in `reasoning_summary`.

Canonical failure: HEI Alex text assertions cited draft turns 19/51; actual sent text finalized turn 56. First-match attribution is unsafe.

### 2. Peer-evidence integration

Primary source silence ≠ no evidence. Before `[UNGROUNDED]` enrichment, search peer streams: SMS/text, email/Sent, prior transcripts, screenshots/portal evidence, call notes.

Procedure:
1. Identify primary source.
2. `cortex(search)` entity/topic/date window across peer evidence.
3. If corroborating peer evidence exists, cite both primary silence and peer corroboration.
4. If absent, write: “contract silent on X; no peer evidence located in [streams searched]” — not “X is ungrounded.”

Canonical failure: Splitero resplit mechanism was labeled ungrounded from contract silence, but SMS confirmation existed.

### 3. Confidence calibration

If `transcript_warnings` non-empty on `session_close` 201, downgrade:

| Normal | Downgraded |
|---|---|
| confirmed | believed |
| believed | suspected |
| suspected | hypothesized |

If ratified-form walk-forward or peer-evidence check is ambiguous or incomplete, stage rather than confirm.

### 4. Document/entity existence check

Before citing typed IDs (`document:*`, `account:*`, `case:*`, etc.) in enrichment claim/evidence:
1. `entity_get` each typed ID.
2. 200 ⇒ cite normally.
3. 404 ⇒ do not substitute/omit. Report sub-finding: entity referenced in transcript but absent from Cortex; recommend create before citation. Stage assertion.
4. Transcript names a document/path with no entity ⇒ report registration gap; absence of citation is not evidence of non-existence.

Canonical failure: written estimate artifact existed in archives but lacked `document:*`; enrichment treated missing citation as non-existence.

## Enrichment assertion contract

∀ enrichment assertion emitted:
- `transcript_depth="verbatim"` or emit nothing;
- `derivation_type="inference"` for synthesis claims;
- `evidence_uris` include ratified turn, source transcript entity, and peer evidence if used;
- `confidence` reflects downgrade table;
- `review_status="staged"` when any core discipline is ambiguous/incomplete;
- never supersede existing assertions; enrichment is additive, not corrective.

## Anti-patterns

Do not:
- enrich `light`/`none` transcripts;
- cite first occurrence of multi-turn topic;
- mark ungrounded from primary-artifact silence alone;
- treat missing typed entity as artifact non-existence;
- supersede during enrichment.

## Related

`session-close` (Step 6 pointer, depth selection) · `session-close-audit` (pre-close gating sibling)

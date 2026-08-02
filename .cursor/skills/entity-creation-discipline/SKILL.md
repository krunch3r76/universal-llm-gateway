---
trigger_match_terms: ["entity-creation-discipline", "entity_creation_discipline", "entity_create", "typed", "source", "claim", "cortex-planning", "cortex", "tool", "call", "description"]
description: "On any cortex(tool='entity_create') call where the description includes a typed claim about the source artifact (mailed, scanned, official, signed, verified) — read this skill before calling."
---

# Entity-Creation Discipline

**Version:** 1.1-compressed  
**Authority:** all agents calling `cortex(tool="entity_create")`.

Companions: `cortex-entity-restructure` for split/migrate; `entity-lifecycle-discipline` for scope type; `session-close-audit` for post-hoc gaps; `document-lifecycle-tracking` for document class; `lawyer-stance` for legal citation verification.

## Purpose

Entities calcify. `description` claims become substrate for future reasoning. `verify_pre_create` is cheap; `correct_post_calcification` requires update/supersession/downstream cleanup.

This skill has two independent pre-create gates:
- **A. Coverage check:** every `entity_create`; prevents same concept under fresh slug.
- **B. Source-verification gate:** fires when description/fields make typed claims about a source artifact.

## A. Coverage check — before every `entity_create`

`entity_create ⇒ search_existing_coverage_first`.

Run both before create:

```python
cortex(tool="search", arguments='{"query":"<name + description keywords>","limit":10}')
cortex(tool="entities", arguments='{"type":"<proposed_type>","query":"<name keywords>","limit":10}')
```

Decision table:

| Signal | Action |
|---|---|
| same concept, different slug | `entity_update` existing or `entity_merge`; do not fork |
| same concept, thin/stale | extend existing with `assert` / `entity_update` |
| related but distinct | create sibling + `relationship_create` (`related_to` / `child_of`) |
| no relevant hit | proceed; source gate may still fire |

Do not rely on exact-slug 409; it cannot catch same-concept/different-slug. API `collision_warning` (≥0.85 near duplicate) is WARN-only/fail-open; agent judgment remains required.

Anti-patterns: slug-as-uniqueness; skipping search on “obvious” creates; create-then-merge as default.

## B. Source-verification gate

### Trigger

Fires when proposed `description` or structured field contains a **typed claim** about the underlying artifact: what it physically is, where it came from, how produced, or whether authenticated.

Watch phrases and near-synonyms:
- paper/mailed/physical/hard-copy bill/notice/letter;
- scanned document/bill/letter;
- official notice/bill/letter when “officialness” is an artifact property;
- signed/executed/signed and dated;
- system record, portal screenshot, TCAS portal, vendor output;
- verified/authenticated/certified/notarized;
- original, as filed/recorded/served/submitted.

Neutral language does not fire the typed-claim gate: “document at `<uri>`,” “PDF at `<path>`,” “PNG sourced from `<directory>`.” Use neutral wording when verification is incomplete.

### Satisfaction paths — at least one required

#### 1. Visual verification (strongest)

Open/render source via `fs(op="read")` and vision/extraction if needed. Confirm visual markers.

For tax bills: TCAS portal markers (menus/header/footer/system UI) and paper-bill markers (county seal, bill stub, return envelope text) are non-overlapping. Seeing neither is not verification.

#### 2. OCR-grep verification

Read existing `.extracted.md` or run extraction; grep type-exclusive markers.

Examples:
- paper bill: `DETACH`, `RETURN WITH PAYMENT`, `PLEASE MAIL TO`, `RETAIN FOR YOUR RECORDS`;
- TCAS portal: `DTAC TAX COLLECTION SYSTEM`, `View Tax Bill`, `Bill Detail`;
- official notice: `NOTIFICATION OF`, `OFFICIAL NOTICE`, appeals-office letterhead.

Text-only is acceptable only when markers are unambiguous. Ambiguous/empty OCR ⇒ use path 1 or 3.

#### 3. Provisional flag (always available)

Create neutral/provisional entity and queue verification before promotion:

```python
cortex(tool="entity_create", arguments='{"id":"document:<slug>","type":"document","name":"<neutral name>","description":"Source artifact at <uri>. PROVISIONAL: typed identification not verified at create time. See todo:verify-document-<slug>.","source_uri":"<uri>","status":"provisional"}')
cortex(tool="entity_create", arguments='{"id":"todo:verify-document-<slug>","type":"todo","name":"Verify typed claim on document:<slug>","description":"Open source at <uri>; confirm visual markers; promote document:<slug> and add typed claim, OR retract.","workflow_state":"open"}')
```

If none satisfied: omit typed claim. Use neutral language and state pending verification.

| Bad | Good |
|---|---|
| “Mailed paper Secured Escape Tax Bill issued 2025-12-12.” | “Source artifact at `<uri>`; typed identification pending verification.” |
| “Scanned BOE-266 filed 8/27/2025.” | “Source artifact at `<uri>`; outbound mailing claim pending visual verification.” |
| “TCAS portal screenshot of suffix-31 bill.” | “Source artifact at `<uri>`; rendering origin pending visual confirmation.” |

## Anti-patterns

- Filename/directory transitive trust (`tax_bill_*` ≠ verified tax bill).
- Index inheritance as verification.
- OCR sidecar treated as visual verification.
- Skipping gate because claim looks obvious.
- Running gate after create; if post-create fails, update to neutral/provisional, then verify before sharpening.

## Relationship to audits

`session-close-audit` detects missing `source_uri` / unwired entities after the fact. This skill prevents bad entities before write. Applying it reduces close-time gaps and inherited cleanup.

## Minimal operating summary

Before every create: search coverage. If description makes typed source-artifact claim: visual verify OR OCR-grep exclusive markers OR create neutral provisional + verification todo. Otherwise use neutral language.

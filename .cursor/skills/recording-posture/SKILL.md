---
name: recording-posture
description: "On durable life observation — route fact→assert, synthesis→journal, position→checkpoint; event_anchor re-talk; capture≠extraction; no diary obligation. Recorder seat owns ingest lane separately."
trigger_match_terms: ["recording-posture", "recording_posture", "event_anchor", "re-talk", "life map", "capture vs extract", "Recorder", "journal capture"]
related_skills: ["journal-digest", "life-imprint-when-how", "cortex-provenance-discipline", "evidence-review-discipline", "matter-playbook-lifecycle"]
---

# Recording posture

Universal floor for durable observation capture on **every** seat. The **Recorder** seat owns the life-ingest lane and chronicle craft — this skill does not collapse into that seat.

**Authority binds:** operator 2026-07-18 (journaling purpose + event identity) · refined map B · `traid-agency-protocol` § Recorder.

## Invariant

```text
∀ durable-worthy observation crossing any seat:
  route(kind) before losing the turn
∧ map_is_SOT ∧ words_are_provenance
∧ capture ≠ extraction
```

**Purpose:** journaling **updates the model's sparse life map** (graph assertions + edges). Operator words are kept as **provenance**, not as the primary memory surface.

## Route table

| Kind | Route |
|---|---|
| Concrete fact | `assert` / `observe` on host or entity (map pin) |
| Broad synthesis | matter `journal.md` or `notes/journal/<subject>/<date>.md` → later digest/extract |
| Program position | ring checkpoint + resume-bind sidecar |

## Event identity (PRIORITY)

```text
event_anchor := YYYY-MM-DD#kebab-slug
```

Date alone is insufficient. Re-talking the same event **must** reuse the anchor:

| Intent | Map action | Words |
|---|---|---|
| Add | new assertion(s) under same anchor | append / revise section prose |
| Correct | `supersede` prior claim | amend section; prior claim retained in chain |
| Change | supersede / retract as needed | same |

**Falsifiers:** parallel `#` slug for the same happening · silent overwrite without supersede chain.

SOT: `cortex://notes/system/threads/5329-journal-event-identity-v0.md`.

### Known-state ack (substrate floor)

Within life/Recorder scope, the assert-create substrate blocks same-anchor **near-verbatim / high-lexical** re-dumps before INSERT:

- HTTP 200 + `was_new=false` + `already_known=true` + `known_state_reason` + `matched_assertion_id`
- Seats **must not retry** the same capture on `already_known` — treat as quiet ack
- **`force=true` + valid `supersedes_id` is the reliable CORRECTION escape** — always writes even when lexical score ≥0.85 vs the prior
- Meaning-delta re-talk with SequenceMatcher **<0.85** may still mint (lexical residual — v1 does not claim semantic same-meaning closure)
- Imprint remember/propose share the same helper; propose emits `graph.recorder.already_known` on skip
- Post-insert `near_dup` flags remain **advisory only** — never promoted to write-block

## Capture ≠ extraction

1. **Capture** — get operator words onto a durable journal/matter surface this turn.
2. **Extract** — digest/revision pass updates the map (propose → batch-approve).
3. Defer extraction when marked; never treat undigested prose as authoritative while the graph stays empty.

Compose: Use the `journal-digest` skill — first ingest + **revision pass** on `content_sha_changed`.

## Selection floor (every seat)

- Provenance classes · ¬ silent inference
- **Selected capture** over total lifelog (Sellen / MyLifeBits foil / SIS)
- **No diary obligation** — quiet weeks are fine
- Sparse trail over verbatim hoard

## Never

- Collapse this skill into the Recorder seat (or into the parked strategist/psychologist seat)
- Persona / identity imprint ("be Valentina") — Valentina ≡ Recorder **duty station**, not a cast persona
- Graph/GCal/passive connectors in v1 (deferred)
- Matter policy invention (charter / matter playbook owns that)
- Treat journal prose as SOT while graph assertions lag

## Seat split

| This skill | Recorder seat |
|---|---|
| Universal floor + routing + map-vs-provenance + event identity | Life-observation lane, chronicle craft, digest/extract machinery, weekly residue optional |

Protocol: `cortex://notes/system/design/traid-agency-protocol.md` § Recorder  
Research: `cortex://notes/system/threads/5329-refined-map-B.md` · zoom-out `5329-zoom-out-consult-answer.md`

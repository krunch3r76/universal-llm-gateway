---
name: composer-standing-reply-format
description: "Operator-activated standing reply format — six-slot shape, voice, density, gloss/acronyms, multimodal gates. Use when the operator enables it for this session."
disable-model-invocation: true
---

# Composer Standing Reply Format

**SOT:** this file. Cortex convention paths below are vestigial redirects only.
**Activation:** operator invokes once per session (`Use the composer-standing-reply-format skill`).
`∀` substantive operator-facing reply thereafter: bind this skill. ¬ auto-load on boot.
**Exempt:** tool-only turns, bare acks, code-only citations, terse-mode, or operator drops format for a turn.
**Audience:** human operator — conversational voice; density cuts noise, not personality.

## Voice

- **Full natural speech is the default** — not a nice-to-have, not “OK if you feel like it.” Write as you would talk to a teammate: complete sentences, ordinary cadence, plain words before jargon.
- Six-slot **headers** stay; the prose *under* each header should sound spoken. Telegram fragments, stacked noun phrases, and mechanism-first telegraphese are format misses even when the six slots are present.
- Warmth comes from real wins and clear orientation; ¬ cheerlead empty progress; ¬ perform friendliness.
- Concision cuts **noise** (raw IDs, mechanism, bookkeeping), not personality and not natural speech.
- `correct_six_slot_shape ∧ stiff_or_telegram_prose` ⇒ Voice miss — fix the voice, don’t drop the slots.

Operator correction class (2026-07-19): “reply more naturally / verbal communication” — seats were treating Voice as optional permission (“full natural sentences OK”) and defaulting to stiff slot-fill. This section is binding when the skill is active.

## Gloss (proper nouns ∧ acronyms)

`∀` label, gate name, fixture, code id, **acronym, or initialism** on first use in an operator-facing reply:
expand in place or gloss in 3–6 plain words — even if it appeared earlier in a bus thread, checkpoint, sibling session, or scoreboard.

| Pattern | Example |
|---|---|
| Expand then bare | `RFR (Request for Reinstatement)` → bare `RFR` OK later **in that same reply** |
| Bare then expand | `Request for Reinstatement (RFR)` |

Applies equally to matter shorthand (RFR, EO, AAB, …), infra labels (CDP, WIP, …), and gate IDs.
`¬` assume shared memory from a closed thread, sibling session, or scoreboard slug.
`correct six-slot shape ∧ unglossed acronym` ⇒ format miss.

## Six-slot shape

1. **Where we're headed / problem → direction** — the arc grounded as **problem(s) we're solving → direction of travel**, not a narrative that assumes the operator still holds the thread. Serves the vision through-line. On checkpoint / orchestration resume, this slot **must** restate charter + current state first (see Checkpoint / orchestration resume); when the fork spans sessions, follow with a **surface · status · aims-to** card (see Re-entry orientation) — enough that a cold, parallel-session reader knows the charter, where it stands, and where we're heading before the decision line.
2. **Decision I need** — one line: the fork + recommended default.
3. **The win / working now** — outcomes (and earned momentum), not raw IDs.
4. **Still in our way / blocked** — numbered; consequence-first (cost / what it blocks), ¬ mechanism-first.
5. **Your call** — choices restated + any config to confirm.
6. **Receipts / detail pointer** — one line to checkpoint / IDs / sidecar.

Lead with problem → direction + decision. Operator may stop after the first two lines and still know the problem being solved, the direction, and the fork.

## Checkpoint / orchestration resume (charter + state)

Operator correction class (2026-07-19 / friction 25419): seats reconstituted from CHECKPOINTs internally (orchestrator-workflow R12 resume step 0) but opened the operator turn without restating the charter — even with this skill active. Internal reconstitution ≠ operator-facing orientation.

**Profile gate (binding — todo:orchestration-resume-charter-print):** Continuity-root resume always opens with **`Mission:`** + In/Out (`decision:continuity-resume-mission-open`). The rest of this section binds **`orchestrator_continuity`** only (¬ root tagged `charter-runner` / `tick_charter`). Discriminator: `agent-bus-discipline` § Two CHECKPOINT profiles. **`tick_charter`** resume: Mission + Scope, then tick index (wave · in-flight · next pickup) — ¬ the state walk. Composes with `operator-posture` Rule 3.

**Fire when any holds (and profile = `orchestrator_continuity`):** reply after a CHECKPOINT turn, `resume <thread#>`, pasted orchestration handoff, or any standing-root pickup that has a charter/brief/scoreboard/Objective.

**Slot 1 MUST lead with**, in spoken prose, before surfaces detail or the decision line:

1. **`Mission:`** — the root's **original** Objective in one sentence (what success is) plus **In** / **Out**. Same referent as operator-posture § Charter / mission referent — not a mid-session seeded todo, parked friction, or slug.
2. **Current state** — compact vs that mission: what's settled · what's live · what's next (≤3 short clauses). Outcomes and position, not mechanism and not a scoreboard row walk.
3. **`In one line:`** — one explicit labeled sentence distilling what the arc/session is doing (after Been→Are→Going when rule 1 orientation precedes slot 1; todo:checkpoint-resume-one-liner). The scan line — not a substitute for mission + state above.

Surfaces card (below) may follow when ≥2 live surfaces; it does **not** replace mission + state + In one line.

| Miss | Why |
|---|---|
| Jump to decision / WIP / fork with no Mission + In/Out | Cold reader cannot agree the lock |
| Surfaces-only open with no charter purpose | Status of pieces ≠ charter of the whole |
| A1–A8 / slug inventory as “state” | Scoreboard dump (still banned); state is a spoken position line |
| Mission + state without explicit `In one line:` | Missing scan sentence (todo:checkpoint-resume-one-liner) |

`checkpoint_or_orchestration_resume ∧ slot-1-omits-(mission ∨ current_state ∨ in_one_line)` ⇒ format miss.

Composes with operator-posture: `¬` dump scoreboard rows **≠** omit mission. Inventory dump remains a miss; mission silence is the other miss.

## Re-entry orientation (multi-session operator)

**Invariant:** the operator runs many sessions at once — `¬` assume working memory of this thread. `∀` substantive reply on a multi-session or multi-surface arc: after charter + state when a resume trigger fired, slot 1 must also ground **without a story**, via a compact surface card:

`<surface> · <status> · aims to <purpose>`

- One row per live surface in play (existing rule/skill/artifact, proposed change, in-flight consult/map).
- **Status** is a single word/phrase (`live`, `proposed`, `being weighed`, `ready`, `blocked`), never a paragraph.
- **aims-to** is the problem that surface solves — the direction, not the mechanism.
- Prefer the table modality when ≥3 surfaces are live (does not consume the one-visual budget in an ordinary way — this IS the orientation, not decoration); prose card of 2–3 lines otherwise.

`multi_session_arc ∧ slot-1-is-narrative-not-card` ⇒ format miss (operator cannot re-enter).
`multi_session_arc ∧ resume_trigger ∧ slot-1-skips-charter-state` ⇒ format miss (surfaces without charter).

## Density discipline

| Rule | Bound |
|---|---|
| One line per item, max | second clause → usually mechanism → move behind receipts |
| Outcomes, not steps | win slot = what's proven; ¬ narrate step sequence |
| Glosses stay 3–6 words | ¬ grow into sub-clause definitions |
| Blockers = consequence only | "Can't run X, so Y is the cost"; how lives behind pointer |
| Bold only slot headers | ¬ inline bold labels |

Raw ledger/staging/assertion IDs stay behind the receipts pointer.

## Anticipatory clarification

When the reply introduces a **fork, boundary, or “X but not Y” contrast**, add **one spoken sentence** that answers the obvious follow-up (“wait — why?”) in the same turn — plain language, before Receipts.

- Complements Density: outcomes + the *why of the split* stay in body; deep mechanism still goes behind the pointer.
- One layer only — not a FAQ, not architecture doctrine (that stays in consult/infra skills).
- Miss: stating a dual path (MCP vs HTTP, handoff vs generate, attended vs unattended) with no sentence for why the split exists.

`introduces_contrast ∧ ¬why_sentence` ⇒ format miss.

## In-flight step naming (model + substrate)

Operator correction class (2026-07-19): bare phase labels (“discovery”, “scoping”, “ingest”, “dispatch”) without **who is running them** leave the operator unable to judge cost or trust.

`∀` operator-facing mention of an in-flight or just-completed work step (discovery · scoping · ingest · review · generate · handoff · path-sim leg · bus-nudge · implement):

Name **model family + substrate** in the same sentence (or the same win/blocker line) — plain words first; wire slug only behind Receipts if needed.

| Required in prose | Example (good) | Miss (bad) |
|---|---|---|
| What step + which model + which path | “Literature discovery is running on GPT-5.5 via Stargate API with live web tools.” | “Discovery is in flight.” |
| Same when recommending a default | “Next: scoping on Opus via CDP (Chrome DevTools Protocol) on web-anthropic — preferred over GPT-5.5 API.” | “Next: scoping pass.” |

**Minimum bind per mention:** `<step> on <model-or-family> via <substrate>`  
Substrate gloss on first use in the reply (CDP · Stargate API · cursor-sdk · in-seat · …).

`bare_phase_label ∧ ¬(model ∨ family) ∧ ¬substrate` ⇒ format miss — even if six slots and Voice are otherwise correct.

Does **not** waive Density: keep it one spoken sentence; put execution IDs and wire model strings behind Receipts.

## Multimodal gates

`ratified(by=opus, ref=cortex://notes/system/threads/5352-g5-opus-ratify-multimodal-gates.md)` · 2026-07-19.

Extends the six-slot shape; does not replace it. Prose is always the default.
Select a structured visual by the **decision object** (what the operator must decide), never by volume; thresholds are proxies — never pad to trip one.
At most one structured visual (mermaid | table | canvas) per reply unless the operator asks; emoji is a tenor channel and does **not** consume that budget.
**Placement:** visual **after** decision line, **before** wins.

| Modality | Fire when | Bounds | Evidence |
|---|---|---|---|
| Mermaid / UML | ≥3 named components whose deps the operator must hold to decide, OR a state/sequence flow is the fork | 1 diagram, decision-cluster scope; glossed labels, no raw IDs; diagram = relations, bullets = outcomes | graphologue-interactive-llm-diagrams; llm-concept-mapping-cognitive-load; structsum-faster-text-comprehension (node-link inference; no direct mermaid-in-chat study) |
| Table | ≥3 options × ≥2 attributes; 2-option forks stay prose | ≤6 rows × ≤4 cols; cells ≤6 words; detail behind receipts | structsum-faster-text-comprehension; autoform-beyond-natural-language (reading-comprehension; decision transfer = labeled inference) |
| Canvas | Reply IS the deliverable (artifact asked or inherently visual); else offer one line when content is artifact-shaped (interlinked material the operator would otherwise rebuild from many coupled bullets) and produce only on acceptance | Never unrequested for status / checkpoint / yes-no | retrace-interactive-reasoning-visualizations; structsum-faster-text-comprehension |
| Emoji | Default OFF; session opt-in only | ≤3, fixed slot markers (governance, ¬ evidence-backed); never inline; never replacing a gloss | No KEEP paper shows emoji cognitive benefit; peft-emoji-personality-llms (DEMOTE) = overuse-risk color only |
| Prose | Always — the default | Six slots + density discipline | when-to-think-when-to-speak-disclosure; interactive-llm-reasoning-verification-ui |

### Anti-patterns

- Mega-diagram of a whole status/thread
- Structure restating prose verbatim
- Modality stacking without operator request
- Mechanism / raw IDs in body, nodes, or cells
- Canvas for yes/no or single-blocker replies
- Emoji while OFF, inline, or gloss-substituting
- Threshold gaming (padding a 2-option fork to 3 to earn a table)
- Claiming diagrams aid the seat's own reasoning (gates govern operator-facing rendering only)

### Falsifiers

- Diagram gate falls if scoped diagrams don't beat prose on relational forks, or merged overviews beat scoped
- Table gate falls on accuracy loss or slower decisions (STRUCTSUM = reading-comprehension; decision transfer = inference)
- Canvas trigger moves on offer telemetry: always-accepted → promote to produce; unused → tighten to request-only
- Emoji default falls if ingested evidence shows boundary markers cut scan time without noise
- One-modality budget falls if stacked structures beat the best single structure
- Global rollback: if gated replies measurably slow operator decisions vs prose-only, multimodal layer reverts — six-slot base untouched

## Home rule

`skill = law` (this file). Cortex convention URIs are redirects only:
- `cortex://notes/conventions/composer-standing-reply-format.md`
- `cortex://notes/conventions/composer-standing-reply-format-multimodal-gates.md`

`¬` dual-home; `¬` "convention wins over skill"; `¬` mandatory cortex read for routine binds.
Entity: `agent_skill:composer-standing-reply-format`.

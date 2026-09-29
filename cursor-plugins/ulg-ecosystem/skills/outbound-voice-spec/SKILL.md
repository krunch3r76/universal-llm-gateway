---
sot: claude
name: outbound-voice-spec
description: "Ghostwriting outbound text the operator will send: bind OUTBOUND SPEC and attested signer facts — never assign model identity. Pair prose-discipline."
trigger_match_terms: ["outbound-voice-spec", "outbound_voice_spec", "ghostwrite", "write as me", "my voice", "send-ready", "outbound reply", "wconnect", "correspondence draft", "first person outbound", "who are you writing from", "drifting from the objective", "love language", "operator voice", "message to send", "OUTBOUND SPEC", "speaker card"]
---

# Outbound Voice Spec

`¬ instruct(model_identity)` · `⇒ specify(output_artifact)`.

claude.ai resists identity assignment ("you are my pen", "act as the signer") — persona negotiation without changing the draft. Describe the **message the operator will send**, not the **seat writing it**.

Load **`prose-discipline`** for register, antecedents, and AI-trite bans. This skill binds **task shape, material boundaries, and engagement** before the first sentence.

## Transmit

A message in the signer's name stays a draft until the operator says submit. That word comes before any form post, mail, fax, or email. The draft uses only claims the operator has stated that are already recorded on the matter. A gap is asked and recorded before a sentence depends on it.

## When

| In scope | Out of scope |
|---|---|
| Outbound text operator will paste or send: workplace comments, replies, emails, texts, forum posts, vendor chat | Direct chat with operator (coaching, planning) unless deliverable is still a named outbound artifact |
| claude.ai Chat or Cowork when skill attached or invoked | Code-lane work with no outbound artifact |

## Mechanisms L1–L6

Map each mechanism to **OUTBOUND SPEC** fields or session placement. None assign model identity.

| Mech | Binds | Operator supplies | Model does |
|---|---|---|---|
| **L1 Speaker card** | Signer · Standing limits | Attested role boundaries; what signer may not claim | First person = signer only; never writes past limits |
| **L2 Pinned objective** | Objective · Objective priority | One line: what message must accomplish | Cuts clever angles that miss the pin |
| **L3 Interlocutor stance** | Deliverable row | "One send-ready body; no options; no commentary" | Editor duty — artifact only, not analyst about signer |
| **L4 State placement** | Project / Customize / session open | Standing biography, skill attachment, project instructions | Persists context across phone sessions; ¬ per-draft identity fight |
| **L5 Pre-draft ledger** | Material attested · Voice anchor | Exhaustive facts signer swears to; optional cadence sample | Builds from attested set only; pair **`no-silent-inference`** |
| **L6 Verifier pass** | Spec amendment · correction | "Drifting from objective" or factual fix | Treat as spec update; redraft once; no apology cascade |

Correction is **spec maintenance**, not relationship drama (`engagement-stance`).

## OUTBOUND SPEC (fill before drafting)

Every field describes the **artifact** or **attested facts**, never the model. Blank field ⇒ **forbidden to invent** for that dimension.

```text
OUTBOUND SPEC
Signer (first person inside the message only): <name>
Recipient / thread: <who reads it>
Objective (one line): <what this message must accomplish>
Material attested by signer (exhaustive — do not add facts):
  -
Standing limits (signer must not claim or imply):
  -
Voice anchor (optional — 1–3 sentences to match cadence, not content to copy):
Objective priority: if conflict, <objective> beats <new angle>
Deliverable: one message body, send-ready; no preamble; no options; no commentary after
```

## Copy-paste preambles

**L1 — Speaker card** (duty framing; paste above OUTBOUND SPEC or into Project instructions):

```text
SPEAKER CARD
Duty: produce one send-ready outbound message body for the signer named in OUTBOUND SPEC.
Do not assign a persona to the writing seat. Do not narrate the drafting process.
Signer facts and limits come only from OUTBOUND SPEC fields below.
```

**L4 — Ghostwrite Project placement** (claude.ai Project instructions or Customize preamble):

```text
Ghostwrite sessions — standing setup
Skills: outbound-voice-spec, prose-discipline
Session open: standing biography (role, relationships, claim limits) — not per-draft "you are …" lines.
Every draft: operator completes OUTBOUND SPEC before the first sentence.
Deliverable: one message body ready to copy; no A/B options; no postscript about what the seat did.
```

**L5 — Pre-draft ledger** (when material is long; paste inside operator message):

```text
PRE-DRAFT LEDGER (attested only — do not infer beyond this list)
Material:
  -
Voice anchor (cadence sample, not content to copy):
  -
```

**L6 — Verifier pass** (internal checklist before output; optional paste for liaison packs):

```text
VERIFIER (before sending body to operator)
□ First person = signer throughout
□ No facts outside Material attested
□ Objective pin satisfied; no unpinned new angles
□ Standing limits respected
□ No meta, options, bolted-on closer, or "here's a draft you could send"
```

## Required output · forbidden instructions

| Required | Forbidden |
|---|---|
| Only message body (or one labeled copy block) | "You are …" / "You are not …" / "Act as …" / "Be my pen" |
| First person = signer in spec | "Write as me" without OUTBOUND SPEC |
| No meta about seat's process | Options A/B/C when one sendable message asked |
| Missing fact ⇒ one `[NEED: …]` or bracketed question in draft — not a menu | Inventing material from undisclosed records |
| No bolted-on closer (`prose-discipline`) | Analyst voice about the signer |

## Failure patterns

| Symptom | Likely cause | Fix in spec |
|---|---|---|
| Wrong standing (claims role operator lacks) | Weak **Standing limits** | List what signer may not claim |
| Tidy argument operator didn't supply | Model filled from context | Tighten **Material attested**; add objective priority |
| Blame shifted onto signer | Invented narrative | Attested material only; name inversion in limits |
| Ignores thread objective | No **Objective pin** | One-line objective + priority row |
| Flat after corrections | Identity fight instead of spec | Resend amended OUTBOUND SPEC; drop "who you are" lines |

## Before / after (instruction form)

Before (triggers persona negotiation):

> You are the signer. Write as me in my voice. Be my pen, not an assistant.

After (artifact-bound):

> OUTBOUND SPEC — Signer: User · Recipient: hiring manager · Objective: confirm interest without overstating pharmacy experience · Material attested: … · Standing limits: may not claim licensed pharmacist · Deliverable: one send-ready body

## Life sessions — standing biography

**Scope gate (OR):** standing biography + session-open recall architecture applies when **either**:

| Branch | Examples |
|---|---|
| **claude.ai directly** | Chat, Cowork on phone/desktop without code checkout |
| **Life-representation seat** | Cursor IDE liaison on correspondence, `vortex-life` MCP, agent_bus on personal/outbound work |

**Out of scope:** code-lane engineering without life representation → per-task OUTBOUND SPEC packing only.

When in scope (~90% of life sessions), **standing biography is default-on**: role, relationships, what signer may/may not claim. Failure mode = session **lacks** it, not overload.

### Session-open placement (canonical)

| Session type | Standing biography | Matter recall | Speaker / person recall |
|---|---|---|---|
| In-scope life (either OR branch) | ON | `recall(op="matter", q=<matter>)` after `cortex_brief` when a named matter applies | Only when outbound ties to a graph entity |
| Ghostwrite (signer ≠ operator) | ON — mark with `SOURCE: … — context, not material` | Same as above when matter named | Recall **speaker** entity when one exists; not bare person-entity recall seeds |
| Code-lane, no life rep | Per-task OUTBOUND SPEC only | Skip unless task names a matter | Skip |

| Layer | Surface | Loads |
|---|---|---|
| Session orientation | `cortex_brief(seat="web-anthropic", role="lead"[, principal="person:<slug>"])` once | Lean seat card; optional `principal=` when project instructions pin operator person entity |
| Task-bound slice | OUTBOUND SPEC **Signer** + **Standing limits** each draft | Attested for *this* message only |
| Matter facts | `recall(op="matter", q=<matter>)` when outbound ties to hub correspondence | Scoped factual recall only |
| Persistence | claude.ai **Project instructions** or Customize skills (L4) | Across phone sessions |

**Avoid:** bare person-entity recall seeds (e.g. `recall(op="matter", seeds=["person:<user-entity>"])`) alone as standing boot — cross-domain graph noise invites invention from records not attested for this message.

### Operator person entity (graph binding)

Standing biography in project instructions is **attested prose** (role, relationships, claim limits) — it does not auto-resolve a `person:` entity.

When project instructions include an **Operator person entity** line (`person:<slug>`), pass it at session open:

`cortex_brief(seat="web-anthropic", role="lead", principal="person:<slug>")`

That projects `attributes.durable_identity` and temporally active `legal_matter:*` rows into the boot head block. Without a configured entity id, skip `principal=` — do not guess from search, aliases, or `person:operator`.

Per-draft **Signer** in OUTBOUND SPEC remains the outbound first-person anchor. Ghostwrite (signer ≠ operator): recall the **speaker** entity only when OUTBOUND SPEC or an explicit seed names one — not bare person-entity recall as standing boot.

### Ghostwrite (signer ≠ operator)

Standing operator biography stays **on** for session orientation; leak defense is **marking**, not withholding. See session-open placement table above.

L3 stance uses **duty language** (`duty: editor`), not identity assignment ("you are the editor").

## Liaison (operator not at IDE)

Code-seat packs chat: harvest thread → operator attests material + limits (voice note OK) → liaison inserts **OUTBOUND SPEC** + `Use the prose-discipline skill` + `Use the outbound-voice-spec skill` → deliverable remains **one outbound body** (liaison does not rewrite in liaison voice on the bus).

## Cross-reference

- `prose-discipline` — register, tells, sequitur, outbound slip-checks
- `engagement-stance` — on sharp correction: substance once, no agreement cascade
- `no-silent-inference` — L5 attested-material gate
- `runbook:prose-rhythm` — rhythm pass after facts are right

## Minimal operating summary

Specify the outbound artifact. Attest facts and limits. Pin one objective. L1–L6 before first sentence. Never assign model identity. One send-ready body. Pair with prose-discipline.

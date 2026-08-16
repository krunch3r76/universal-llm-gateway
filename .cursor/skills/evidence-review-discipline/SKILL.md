---
trigger_match_terms: ["evidence-review-discipline", "evidence_review_discipline", "assert", "beyond", "literal", "evidence", "review-reasoning", "assertion", "derived", "visual", "screenshots", "pdfs"]
description: On any assertion derived from visual evidence (screenshots, PDFs, photos), email/SMS threads, or interpreted documents BEYOND literal transcription — read BEFORE writing the assert.
---

# Evidence Review Discipline — Pre-Assert Skeptic Pass

**Version:** 1.0-compressed  
**Authority:** HIGH — any visual/email/document evidence assertion that goes beyond literal transcription.

Companions: `no-silent-inference`, `named-entity-verification-gate`, `document-critique-timeline-discipline`.

## Trigger

Read before writing any Cortex assertion, agent-bus turn, or user-facing summary derived from:
- screenshot/photo/image;
- email body/header/quoted thread;
- PDF/DOCX/document where authorship, intent, or relational structure is inferred;
- chat log/transcript with ambiguous speaker/addressee/quote boundary.

Fires only when the claim goes beyond literal transcription, especially:
- author/sender/signer/approver/initiator;
- addressee or “about whom”;
- intent, motive, stance, sentiment;
- same incident/person/transaction/thread linkage;
- causal/temporal link not literally stated;
- status claim (“submitted”, “approved”, “received”, “paid”) not stamped by source;
- identity unification (`this person = person:foo`).

Bare transcription does not trigger.

## Failure mode

Visual/email evidence invites fluent gestalt. Agent asserts authorship/addressee/intent/linkage without asking: “What does this literally say, and what does it NOT say?” Misattribution then becomes Cortex substrate.

Canonical anchor: session `web-2026-05-12-1650` Bharti/automated-misattribution — literal headers conflicted with contextual gestalt; a 30-second raw-evidence skeptic pass would have caught it. Pattern-mode cannot reliably disprove its own pattern; use a separate literal-mode pass.

## Core protocol

### 1. Read source literally first

Hold exact bytes/text/metadata in context before inference. Do not paraphrase. For images, OCR/transcribe/describe text first; then interpret.

### 2. Call skeptic API

Input raw evidence (image bytes, full headers+body, full quoted text). Use `gpt-5.4-mini`; fallback `cursor/claude-sonnet-5` with same prompt. If unavailable/errors, fail-closed.

Canonical prompt:

> You are reviewing a piece of evidence before another agent asserts a claim derived from it. Your job is the literal layer, not the inferred one.
>
> Answer in two parts:
>
> 1. **What does this evidence literally say?** Quote or transcribe directly stated content. Names, dates, addresses, amounts, addressees, subject lines, signatures — only what is literally present.
>
> 2. **What does this evidence NOT say?** Enumerate plausible inferences a reader might be tempted to draw that are not literally supported. Especially: authorship beyond visible signature/header, addressee beyond visible “To:”, intent or motive, links to other incidents, status claims (sent / received / approved / paid) not stamped by the artifact itself.
>
> Be terse. Do not narrate. Do not synthesize. Refuse to fill gaps.

### 3. Reconcile

| Skeptic outcome | Action |
|---|---|
| confirms literal grounding for assertion | Absorb silently; proceed. |
| flags divergence / assertion exceeds literal support | Surface gap to user; do not silently downgrade or keep original. |
| reads materially different from you | Surface disagreement; escalate. |
| API unavailable/errors | Fail-closed: do not assert; write literal-only claim, mark inference `hypothesized`, or ask user. |

Surface format:

> **Skeptic flag** on `<source>`: I was about to assert `<draft claim>`, but the literal evidence only supports `<narrower claim>`. The inference about `<gap>` is not directly stated. Confirm or correct?

### 4. Write only after reconciliation

Cortex chain:
- `evidence` references raw source path/URI/email/screenshot;
- if skeptic surfaced anything, `reasoning_summary` notes reconciliation;
- `derivation_type=inference` for visual/email-derived claims unless the claim is literal quote/transcription.

## Model / surface decisions

- `gpt-5.4-mini`: cheap, fast, multimodal, aligned with close-time enrichment skeptic pattern.
- Fallback: `cursor/claude-sonnet-5`; never silently skip.
- Confirmation is silent; divergence is surfaced. Surfacing every confirmation trains users to ignore the channel.
- Bias toward firing on marginal inference: cheap confirmed pass beats expensive substrate poisoning.
- Skeptic agreement ratifies only the specific assertion shown, not the broader narrative; re-pass for new claims.

## Anti-patterns

| Bad | Good |
|---|---|
| Read screenshot, assert “Bharti sent this” | Transcribe headers; skeptic pass; reconcile; then assert |
| Skip because inference feels obvious | Run pass; feeling-obvious is failure mode |
| Surface every confirmation | Silent confirm; surface divergence only |
| Skip when API down | Fail-closed: no assertion, downgrade, or ask |
| Run pass after Cortex write | Pre-assert only |
| Use on literal transcription | Skip when claim is exactly literal content |
| Treat agreement as whole-narrative ratification | Re-pass each new inferred claim |

## Related skills

- `no-silent-inference`: universal verify-or-mark rule; this is verification path for visual/email evidence.
- `named-entity-verification-gate`: entity grounding; this is evidence interpretation. Filing-track artifacts may require both.
- `enrichment-quality-discipline`: same skeptic pattern at session close.
- `engagement-stance`: silent-on-confirm, surface-on-divergence.
- `document-critique-timeline-discipline`: sequencing/linkage audit for document-class evidence.

## Non-trigger

- Conversation with no Cortex/write/user-facing derived claim.
- Bare transcription (`the receipt shows $42.10`).
- User-provided inline quote where user is source of truth for their own quoted content.
- Speculative brainstorming explicitly marked draft/exploratory.

## Quick reference

1. Read raw source literally.
2. Skeptic API with raw evidence + canonical prompt.
3. Confirm → silent; divergence → surface; API down → fail-closed.
4. Write with evidence URI and reconciliation notes.

## Minimal operating summary

`inference_from_visual/email/document_evidence ⇒ literal_source_first ∧ skeptic_pass ∧ reconcile_before_write`. Do not let contextual gestalt become Cortex substrate.

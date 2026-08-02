---
description: "Before adding specificity in factual drafting — dates, identities, causal links, internal access — verify sources or mark uncertainty explicitly."
trigger_match_terms: ["no-silent-inference", "no_silent_inference", "inference", "verify", "mark", "silent", "review-reasoning", "factual", "drafting", "timeline", "report", "complaint"]
---

# No Silent Inference — Verify or Mark

Universal factual-drafting discipline.

## Trigger

Read before answering or drafting when adding specificity about facts: dates/times, identities, causal links, event linkage, internal access, submission/delivery status, file existence, account/system behavior, or code runtime invariants (type nullability, return shape, raise behavior, `always`/`never` claims).

Also read when translating recollection into a structured narrative, revising a factual artifact, receiving a correction, or considering wording stronger than the source.

## Core rule

`detail ∉ {express_user_statement, quoted_artifact, deterministic_tool_output, source_file_definition} ⇒ ¬state_as_fact`.

If ungrounded detail is material and confirmable, ask. If noncritical and flow should continue, label as inference. Never present inference as observation.

## Classification gate

Before factual insertion, classify each detail:

- `user_stated`
- `artifact_grounded`
- `tool_observed`
- `source_grounded` (source file signature/type/raise site/runtime definition)
- `inference`
- `unknown`

`classification ∈ {inference, unknown} ⇒ ask_or_label ∧ ¬confirmed_language`.

## Ask vs label

Ask first when the detail is material to liability/blame/accusation/intent, date/time, identity, causation/linkage, internal access, submission/delivery/status, file existence/completion, or easy for the user to confirm.

Label only when minor, noncritical, and readability-improving. Use narrowing language: “This may suggest…”, “One possibility is…”, “If I am inferring correctly…”, “The record does not yet establish…”.

## Never silently infer

- dates or exact times;
- who said what;
- incident-to-complaint linkage;
- insider/internal/unauthorized access;
- document sent/submitted/received;
- file/artifact existence or creation;
- photos, recordings, emails, logs, notes, or evidence artifacts;
- motive, coordination, or causation;
- downstream/executor characterization of scope, done-ness, or coverage — a *claim*, verify via `entity_get`/`fs` before relaying as fact, ¬ a check;
- code runtime invariants: nullability, return shape, raise behavior, `always` / `never` / `must` claims.

Specificity rule: `claim_specificity ↑ ⇒ grounding_required ↑`. One `fs(read)` at the definition site can move a code invariant from inference to source-grounded.

## Drafting pattern

Keep confirmed facts, inferences/possibilities, and unknowns requiring confirmation separate.

Good: “The caller referenced a profile-photo mismatch complaint.”
Bad: “The caller knew insider details about a 5 AM selfie verification.”

Good: “This sequence may suggest the incidents are connected.”
Bad: “These incidents are definitely coordinated.”

## Correction duty

If unsupported inference entered a draft, graph assertion, or user-facing summary:

1. State plainly that prior wording was too strong or unsupported.
2. Narrow or rewrite.
3. Supersede/correct graph assertion if needed.
4. Tell the user exactly what changed.

Do not defend unsupported wording.

## Canaries

Stop and verify before saying: “knew”, “confirmed”, “submitted”, “exists”, “produced”, “generated”, “same incident”, “insider”, “internal access”, exact unquoted timestamps, “non-nullable”, “never None”, “always returns X”, “raises on Y”.

## Pre-ship gate

External-counterparty factual artifacts (legal, regulatory, tax, formal correspondence) require `named-entity-verification-gate` before ship. Producer and ratifier run independently; fail closed if Cortex/tooling unavailable.

## Minimal operating summary

Verify if you can. If not, ask or label. If it slipped in, correct plainly.

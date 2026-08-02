---
name: journal-digest
description: "Journal narrative digest — SEGMENT→EXTRACT→CLASSIFY→ATTACH→DERIVE→EMIT→LEDGER→BACK-CITE; re-talk revision pass on content_sha_changed; P1/P2/P3/P2² provenance classes."
trigger_match_terms: ["digest the journal", "journal digest", "provenance class", "journal-digest", "auto_segment", "revision pass", "content_sha_changed"]
related_skills: ["cortex-provenance-discipline", "entity-creation-discipline", "entity-lifecycle-discipline", "life-imprint-when-how", "matter-playbook-lifecycle", "recording-posture"]
---

# Journal digest

Compressed operating card for turning dated journal narrative into atomic, staged graph claims with provenance.

## Pipeline (one screen)

```text
SEGMENT   dated entry → per-# H1 sections (anchor = YYYY-MM-DD#kebab-slug)
EXTRACT   candidate atomic claims (≤1–2 sentences each)
CLASSIFY  provenance class × assert-vs-prose
ATTACH    resolve entity or propose-queue mint (never silent-mint)
DERIVE    deadlines, references, contacts (dedup-gated), follow-ups
EMIT      staged assertions / entities / relationships
LEDGER    digest-ledger watermark per (anchor, content_sha256); revision rows share anchor
BACK-CITE future journal prose cites [assertion:N]; re-talk revises map under same anchor
```

Use `cortex(op=digest, auto_segment=true, entry_date=…, entry_text=…)` for whole-entry runs; per-section watermarks remain on each `{date}#{slug}` anchor.

## Provenance classes (never collapse)

| Class | Shape | Machinery |
|---|---|---|
| **P1** operator-observed | Operator did/received/witnessed X | user_statement + confirmed |
| **P2** counterparty-reported | «Name/role» stated X — X never bare | user_statement + reported |
| **P3** operator-inferred | Operator infers X because Y | inference + suspected |
| **P2²** nested reported-about-reported | BOTH hops attributed | as P2; never flatten either hop |

Rules: (a) P2 figure and P1 dispute **coexist**; (b) P3 never upgrades to P1 by restatement; (c) preserve uncertainty markers (`name_uncertain`, `phrasing_ambiguous`); (d) never collapse P2² hops.

## Assert vs prose

**ASSERT:** identities/roles, reference numbers, scheduled payments, appointments/deadlines, monetary determinations (attributed), formal notices, commitments/conditions, deduped contacts, operator disputes, policy state changes.

**PROSE:** synthesis, strategy, impressions, counsel to third parties, speculation without operational weight, routine log-class instances.

**Threshold:** state-changes assert; routine instances roll up.

## Trigger + idempotence

- **Default:** session-close hook after evidence settles (`CORTEX_DIGEST_CLOSE_HOOK=1`) — enqueue-only on close; never block close on CDP harvest.
- **On demand:** explicit `digest` op ("digest the journal").
- **Idempotence:** per-section `(entry_anchor, content_sha256)` in digest-ledger → skip on match.
- **Re-talk:** same `entry_anchor`, changed `content_sha256` → **revision pass** (not anomaly halt). Target: keep / revise / remove priors + stage adds; new ledger row with `revision_of`. CDP backend may enqueue `kind=revision_extract`.

## Revision pass (re-talk)

Operator may add / correct / change the **map** under the same `YYYY-MM-DD#kebab-slug`. Words stay provenance; facts move via supersede chain.

```text
content_sha_changed → load priors (digest:{anchor}#) → revision EXTRACT
  → stage revise/remove/add proposals → ledger status=staged → batch-approve
```

- **Mem0 ops vocabulary:** keep=no-op · revise=update · remove=delete · add=add.
- **Never** mint a parallel `#` slug for the same happening.
- **Never** silent overwrite without a supersede / retract proposal.

## Writer authority

All digest writes via **propose → commit** (operator one-glance batch approval). Live supersede / retract happens **only on batch-approve** of revision (or initial) proposals — not during EXTRACT.

## Reader standing

| Journal state | Standing |
|---|---|
| Digested + cited, assertions active | Authoritative-by-citation (verify assertion) |
| Undigested (no ledger row) | Advisory — corroborate before load-bearing use |
| Digested but cited assertion superseded | Historical — successor wins on graph |
| Revision staged, not yet approved | Pending — priors remain active until approve |

Cross-ref: `recording-posture` (event identity + capture≠extraction); `cortex-provenance-discipline`; `entity-creation-discipline` gates A+B; `entity-lifecycle-discipline`; `life-imprint-when-how` (`propose`→`commit`).

## Advisory

Contact/attribute dedup: search assertions on the claim text — not card-surfaced `has_attribute` (until attribute surfacing fixed).

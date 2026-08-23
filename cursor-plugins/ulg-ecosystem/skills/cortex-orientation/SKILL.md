---
related_skills: ["subgraph-render", "cortex-provenance-discipline"]
trigger_match_terms: ["cortex-orientation", "cortex_orientation", "cortex", "call", "boot", "close", "cortex-planning", "calling", "convention", "four"]
description: "Before any cortex(...) call, on session boot, or before session close — calling convention, four canonical ops, confidence ladder, three-gate channel-routing test, retract-don't-supersede discipline."
---

# Cortex Orientation

**Authority:** universal — applies on any `cortex(...)` call, session boot, or session close.

Cortex = shared knowledge graph: entities (`type:slug`), assertions (claims + confidence + provenance), relationships (structural links), session edges (reasoning links with session attribution), journals (episodic memory).

Invariant: `claim_about_known_entity_or_decision ⇒ search Cortex first`. `¬evidence ⇒ ¬assert`. Assertions are grounded facts, not vibes.

## Calling convention

All Cortex CRUD goes through `cortex` with JSON-string `arguments`:

```python
cortex(tool="entity_get", arguments='{"entity_id":"decision:my-slug","intent":"card"}')
```

Cursor shape: `CallMcpTool(server="vortex-code", toolName="cortex", arguments={"tool":"...","arguments":"{...}"})`.

Full op catalog / taxonomies / workflow chains live in `agent_skill:cortex`. Load it for non-trivial calls; do not re-expand the op catalog here.

## Session boot

```python
cortex_brief(family="claude", platform="cursor")
cortex_brief(family="claude", platform="web", transcript_id="web-anthropic-YYYY-MM-DD-HHMM")
```

Hold `session_id` for the session; pass it to `assert`, `supersede`, `edge_create`, `relationship_create`, and `session_close`. Seat slug = `{family}-{platform}`.

Boot audit: live boot returns `audit_dump_path`; read it when the briefing seems incomplete. Inspect another seat via `boot_inspect(...)`; inspect mode writes no audit file. Existing audit dumps live under `notes/system/audit/boots/` named `{family}-{platform}-YYYY-MM-DD-HHMMSS.md` (not retired `web-grok-*`).

## Four canonical ops

```python
cortex(tool="entity_get", arguments='{"entity_id":"decision:my-slug","intent":"card"}')
cortex(tool="search", arguments='{"query":"UDS transport invariant","limit":10}')
cortex(tool="assert", arguments='{"entity_id":"decision:my-slug","claim":"brief evidence-backed claim","confidence":"confirmed","evidence":"source context","evidence_uris":["agent-bus:032"],"derivation_type":"compression","session_id":"cursor-YYYY-MM-DD-HHMM","agent":"cursor"}')
cortex(tool="journal_read", arguments='{"limit":3}')
```

Confidence ladder: `confirmed` = verified/settled; `believed` = high-confidence working assumption; `suspected` = pattern inference; `hypothesized` = speculative.

## Assert / entity write preflight

- **`assert(..., dry_run=true)`** — WARN-only preflight: runs pre-INSERT validation and returns `validation_warnings` with `item: null` / **no row written**. Auditor gaps stay advisory (suppress via `acknowledge_audit_gaps`); missing entity still 404. Drop `dry_run` only after warnings look right. Depth: `agent_skill:cortex` § Assertions write-path; confirmed discipline: `auditor-validatable-confidence`.
- **`attributes` coerce-both** — entity + assertion writes accept `attributes` as a **dict or JSON object string**. Bad JSON / non-object → typed `422` with `detail.error == "entity_payload_invalid"` (+ `diagnostics`). Prefer a real object in the arguments JSON; string form is for already-serialized callers.

## When NOT to assert

Bias: under-assert. False positives entrench, rank in search, and mislead future agents.

Before any `assert`, all gates must pass:

| Gate | Pass condition | Fail route |
|---|---|---|
| Stranger | A stranger querying the entity in 30 days benefits. | Journal/session close. |
| Negation | Claim is not likely wrong/superseded/irrelevant in 24h. | Do not write. |
| Subject-centered | Adds beyond `workflow_state` / status tautology. | Skip. |

Channel ladder:

| Content | Channel |
|---|---|
| Durable empirical finding, invariant, decision contract | `assert` |
| Bug-fix record / “I changed X” | git commit body |
| Session work narrative | transcript + `session_close` |
| Smoke-test pass | nothing |
| Smoke-test fail showing real defect | `friction(...)`, then fix |
| Plan / next intent | `entity_create` todo, not assertion |

Noise you seeded: retract (`assertion_update(valid_until=now)`), do not supersede. Supersede is for load-bearing belief evolution.

## Claim shape

`claim` is an index entry, not a document.

`len(claim) ≤ 1–2 sentences`. No prose blocks, code, numbered lists, or multi-paragraph detail. Put detail in a Cortex sidecar and cite it via `evidence_uris`:

```text
claim: brief claim; "Full context: cortex://notes/system/.../topic.md" if needed
evidence_uris: ["cortex://notes/system/.../topic.md"]
sidecar: fs(cortex, write, notes/system/.../topic.md)
```

Advisory gate: `len(claim) > ~300 ∧ ¬evidence_uris ⇒ shorten claim + write sidecar`. Treat warning as correctness signal.

## Operational gotchas

Live gotchas live on `service:cortex`:

```python
cortex(tool="entity_get", arguments='{"entity_id":"service:cortex","intent":"card"}')
```

Read before schema proposals or unexpected Cortex behavior. New durable gotcha: assert on `service:cortex` with `derivation_type:"agent_observation"`.

## Reflective journal

Active direct-write kinds: `entry`, `reflection`, `revision`, `consolidation`. `kind="handoff"` is retired for direct `rj_write`; forward narratives through `session_close(handoff_prompt=...)` only.

RJ boot surfacing is for epistemic shifts, not operational kickoff imperatives. Phase-checkpointed plans belong in bus sidecar + `handoff_prompt` on close.

## `terminal_facts` — read before recommending (life hubs)

`entity_get` on a `case:` or `account:` hub silently appends a `terminal_facts` block: machine-derived terminal dispositions — `denied(action, party, date)` — gathered by graph walk from the hub, including facts held on neighbour entities (`hop_distance`, `arrival_path`).

**When.** Read it before any material operator-facing recommendation on that hub. The canonical catch: proposing an ask the counterparty has already refused on the record. Live example — `case:chase-escrow-flintridge-2026` carries `denied(spread_extension, chase, 2026-04-29)` and `… 2026-06-26`, while the hub's own `summary_row` still lists spread extension as *requested relief*.

**How — leads, not citations.** Every entry is `machine_derived: true` (`action_enrichment_template_v0`). Before repeating one to the operator:
- check `epistemic_state` — `staged` / `flagged` are unverified;
- read the backing assertion by `assertion_id`: claims are often mid-sentence fragments, and the detector can bind a fragment to a predicate it does not support, or mint a **deadline** date as a disposition date (live: a:24812 renders a 2026-08-04 filing cliff as `denied(…, 2026-08-04)`);
- cite the backing assertion, never the `terminal_facts` entry itself;
- `scope_truncated: true` ⇒ the walk hit `scope_cap`; **absence is not proof** of no denial.

Depth: Use the `cortex-provenance-discipline` skill.

**Not shipped.** Nothing checks a recommendation on your behalf. `pre_speak_contradiction_probe` is design-only (`todo:pre-speak-contradiction-probe`). Enrich-on-read is the entire live mechanism, and it fires only when you call `entity_get`.

## `recall` — life memory front door

**When.** Memory questions on the life seat — "what do we know about X", "remember Chase", "where did we leave off on the tax appeal" — use the sibling `recall` tool, not `cortex.search`.

**How.**

```python
recall(op="matter", arguments='{"q": "chase escrow", "seeds": ["case:chase-escrow-flintridge-2026"]}')
recall(op="continuity", arguments='{"q": "where did we leave the tax appeal"}')
```

Pass `q` and/or `seeds` (either suffices). Response is a read-only `RecallCard`: `resolved` vs `candidates`, typed `nulls`, `disclosure`, optional `continuity`, advisory `_next`. Zero writes through recall.

**Search is not the life memory door.** If you already called `cortex(tool="search", …)` on a memory-shaped question, follow the `_next` steer to `recall`.

**Burst/dispositions are leads.** Same caution as `terminal_facts`: cite backing `assertion_id`, check `epistemic_state` (`staged` / `flagged` are unverified).

**Not G3.** `search` remains on the life cortex enum until measured demotion (Phase 3); this stanza teaches and steers only.

### Find a way (recall → repair → scout → ingest)

Absence and “I don’t know the law” are routing signals, not closers.

```
memory ∨ full_picture ⇒ recall first
thin(card) ∨ _next ⇒ repair ≺ scout
rag_miss ⇒ [unverified] ∧ fire_ingest
complexity ⇏ professional_referral
```

- **Recall first.** Memory-shaped or “full picture” → `recall` before any absence sentence. Do not answer “I don’t have X” from a skipped walk.
- **Repair ≺ scout.** Thin card (`nulls`, `_next.reason` in `{vocab_not_covered, scope_truncated, thin_card, pin_seed}`) → if sibling hubs already exist and hold the fact, `relationship_create` now and re-recall. Do not hire `cursor_request(contract=recon)` as a substitute for a visible missing edge. Scout only when the neighborhood is unknown (`resolver_miss`, candidates-only with no pin, no sibling found).
- **Ingest on rule miss.** `rag` miss → state the working rule as `[unverified]` and fire ingest (life `rag(upsert_article)` or `cursor_request` for scope/bytes). A queue that never fires is a slow “I can’t.”
- **License, not complexity.** A licensed professional is only for acts that require a license (paid-preparer signature, IRS representation / POA, CPA attest). “This is complicated” / “I’m not a CPA” / “I don’t know tax law” is not a closer.

Cursor repairs in-seat when both endpoints are known. Life that will not write edges may hire cursor **to repair**. Hiring cursor **only to recon** when the missing edges are already visible is the defect.

## Related / depth

- `cortex` — op catalog, taxonomies, workflow chains.
- `cortex-provenance-discipline` — citing Cortex substrate.
- `cortex-entity-restructure` — splits/migrations.
- `subgraph-render` — before `render_subgraph`.
- `session-close` / `session-close-audit` — close protocol and gate.
- `entity-creation-discipline` — entity birth verification.
- `boot-execution-discipline` — boot write/verify/report mechanics.

Load `agent_skill:cortex` by entity name when you need depth; do not hardcode paths or reintroduce parallel deep copies.

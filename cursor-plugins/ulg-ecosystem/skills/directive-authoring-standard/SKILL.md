---
name: directive-authoring-standard
description: "Before authoring TYPE: DIRECTIVE on the operator-proxy request lane — required fields, RULING judgment marker, wire-contract enum, mint-then-quote, conductor, negotiation."
skill_category: dispatch-delegation
trigger_short: "TYPE: DIRECTIVE ∨ cursor_request ∨ RULING ∨ conductor commission ∨ negotiation_phase"
trigger_match_terms: ["TYPE: DIRECTIVE", "DIRECTIVE", "cursor_request", "RULING", "judgment marker", "mint then quote", "conductor commission", "negotiation_phase", "wire contract", "assumed_state"]
related_skills: ["cdp-operator-proxy", "conductor", "cursor-sdk-instruction-standard"]
canonical: workspaces://universal-llm-gateway/cursor-plugins/ulg-ecosystem/skills/directive-authoring-standard/SKILL.md
---

# DIRECTIVE Authoring Standard

`TYPE: DIRECTIVE` on the operator-proxy request lane ⇒ ten §2 fields inline ∧ named wire `contract` ∧ mint-then-quote ∧ conductor once framing closes on multi-step work.

Protocol SOT (field tables): `cortex://notes/system/specs/cdp-operator-proxy-v0.md` §2.
CSE / DISPOSITION / interrupt / wake: `cdp-operator-proxy`. Cursor-sdk instruction shape (sibling): `cursor-sdk-instruction-standard`.

## D1 — Required fields

First body line `TYPE: DIRECTIVE`. Inline all ten — sidecar = corpus only (`sidecar_content`; `allow_long_body` is rejected on `request`).

| Field | Content |
|---|---|
| `arc` | root thread + slug + spec step id |
| `assumed_state` | one-line claim inviting contradiction — never given |
| `intent` | one line: what changes in the world |
| `scope` | in-scope paths **and** explicit `out-of-scope:` (own line) |
| `authority` | what cursor decides alone vs what returns |
| `AC` | acceptance criteria verbatim, testable. A judgment AC (a fork the executor must bind) MUST carry an admit-visible **Judgment marker** (below) — prose that merely *is* a fork does not raise. ¬ license `TYPE: OPERATOR_GATE` for `recovery_path=human` / missing supervisor — that is inv 39 implement, not a gate |
| `evidence_required` | what CLOSEOUT must carry to be dispositionable |
| `density` | `dense` \| `sparse` \| `investigate` — operator sets; cursor binds executor |
| `budget` | dispatch ceiling; escalate on exceed |
| `vision` | pillar tags **or** `vision: mechanical — <reason>` |

`contract ∈ {implement, investigate}` ⇒ Auto blocks admit without `vision:` (`vision_field_missing`).
Wire `summary` ≤120 chars ULG so-what (inv 18). `pin(desired_model)` on dense / amend — `auto` forbidden.
Attended bind: wire `require_attended=true` **or** body `require_attended: true` / `executor_bind: attended` (OR).

### Density → executor (operator sets `density` only)

| density | Cursor binds |
|---|---|
| dense | Mechanical implement / dense amend: pin `cursor/composer-2.5`. Verify: `cursor/grok-4.7` (`workflows.auto_judgment`). `contract: implement` stays `mechanical` unless a Judgment marker (below) raises |
| investigate | `cursor/grok-4.7` (`contract: investigate`; omit `model=` resolves this id). Escalate to `cdp/opus-5.5` when the bind needs a CDP seat |
| confer (challenge-seeking) | `cursor/grok-4.7` + `contract: confer`. `cdp/fable` only when Kaywan asks and the challenge needs that width |
| sparse amend | Mechanical amend: pin `cursor/composer-2.5`. Judgment amend: `cursor/grok-4.7` |
| architecture bind | `cdp-operator-proxy` § Architecture-bind — a sequence, ¬ one executor |

Escalate on the class of unknown. **2 failed dispatches on the same AC ⇒ stop** the tier or return blocked.

### Judgment marker (admit-visible) — `contract: implement`

Declaring a judgment fork is not enough. Admit only sees an **opt-in line-start marker**. Unmarked `contract: implement` stays `mechanical`.

**Convention (as landed):** a line that starts (optional indent, optional `- `/`* ` bullet, optional `#{1,6} ` heading, optional `**`) then optional `AC<n> — ` (hyphen / en / em dash) then `RULING` or `RULING AC` / `RULING ACs`. Mid-sentence `RULING` does not raise. Also raise: `density:` / `density_triage:` ∈ `{judgment_required, investigate, judgment, recon_pending}`; line-start `open fork` / `named architecture fork` / `architecture fork`; explicit `handoff:` other than `mechanical`.

**Consequence:** a judgment AC written any other way admits `mechanical`, which skips the reasoning-posture preamble AND redirects a pinned reasoning model onto Composer. `pure-mechanical` is not an admitted contract.

**Coverage (honest):** of 13 `contract: implement` bodies on agent-bus:9470, exactly 1 raises today (turn 302, `AC<n> — RULING`). The other twelve — including genuine withheld-lean forks — classify mechanical. This convention does not make the detector broadly work; it tells authors how to send the signal the detector already looks for.

| Bad (turn 343 AC2 — real fork, does not raise) | Good (same fork, raises) |
|---|---|
| `AC2 — seed 1, and this is the fork.` then a bolded imperative: `**Before you pick, answer this and put the answer first: which direction of error is worse here, and why?**` A mechanical dispatch wrongly classified as judgment-bearing, or a judgment-bearing dispatch wrongly classified as mechanical? Withheld lean. Still admitted `mechanical`. | `AC2 — RULING: which direction of error is worse here, and why?` A mechanical dispatch wrongly classified as judgment-bearing, or a judgment-bearing dispatch wrongly classified as mechanical? Withheld lean. Line-start `AC<n> — RULING` is what the admit path can see. |


### Pipeline-first (agent-workflow ops)

- A claude.ai commission or `TYPE: DIRECTIVE` does not name an MCP tool as the delivery surface for an agent-workflow op (observability, review, version, orchestration step).
- That op lands as `pipelines/{domain}/vN/` even when it is one step.
- Substrate resources (CRUD, lifecycle, byte pipes, high-blast named HITL) stay bare typed HTTP.
- MCP appears only as a thin relay (no business logic in the handler) when the review names it; the DIRECTIVE then names that relay as the review named it.
- Use a pipeline when a human reviews the logic as a unit, or steps and prompts must be recorded for provenance, or the op versions independently of its service.
- Falsifier: a one-step pipeline whose YAML is not a better map than the handler docstring.

## D2 — Wire contract enum

Live enum: `cursor_request` **Contract vocabulary** (do not re-copy). Digest:

`contract` ∈ `answer` \| `confer` \| `investigate` \| `implement` \| `verify` \| `execute` \| `propagate` \| `seed` \| `recon`.
`consult` aliases `confer`. Unknown ⇒ 422 `request_contract_unknown` before the turn is written.

| Contract | Authoring note |
|---|---|
| `execute` | One tier-M allowlisted op (`tool_op:` + `effects_expected:` + optional `tool_args:`); `manage.*` **denied** |
| `propagate` | Restart request — ledger rows + drain-gated `sync_restart`; **not** `execute` + `manage.*` |
| `implement` / `investigate` | `vision:` required. `implement` without a Judgment marker (D1) admits `mechanical` |
| `seed` | Mint closable work item via seed path |
| `recon` | Recon front-half |

```
TYPE: DIRECTIVE
contract: propagate
scope: propagation sync_restart mcp
code_ref: <land SHA or omit for HEAD>
effects_expected: propagation row persisted; restart executed or deferred with reason
density: sparse

## propagation
propagation:
  - service: mcp
    code_ref: <sha>
    safe_window: drain_required
    proof_class: client_visible
    # allow_self_preempt: true  — default; auto-escalates to force on own CSE/MCP busy deferral
    # allow_self_preempt: false — machine-read veto
    # force: true  — optional explicit force
```

**Machine-read vs advisory:** `allow_self_preempt` / `force` on rows; `authority:` prose is advisory.
**Derivation tags:** `derived:` / `import_path:` iff generator-derived.
Codework skill slug in body (`Use the abstraction-layering skill` / `work-item-seed-path`): `cdp-operator-proxy` § Codework lanes.

## D3 — Mint then quote

```
∀ outbound turn body (DIRECTIVE, CLOSEOUT, DISPOSITION, PARKED, debrief, ack line):
  mint(artifact) ≺ compose(sentence containing artifact.id)
```

Mint first; compose after the mint response is in hand.
¬ write a sentence containing an id not read from a response payload.
Self-attestation ("minted before this sentence") ≠ compliance.

## D4 — Conductor commission

`framed(Question) ∧ (≥3 G-row-equivalent ∨ bind-then-compose) ⇒ commission(conductor)` — ¬ drive G-row-by-G-row on this DIRECTIVE loop.

DIRECTIVE names: conductor role, a root thread (`new_slug` or existing `role:root`), charter/scope.

**Transport:** this seat has ulg-code `team_dispatch`. Commission is `team_dispatch(op=generate, seat=cursor-sdk, lane=B, ...)`. The cursor-sdk seat nests its own further dispatches. `cursor_request` is not the wire.

**Reachability:** `contract=conductor` is the admitted commission token on `team_dispatch`. Body prose is the instruction; the token is what admit sees. A non-roaming reasoning model on `mechanical` redirects to `cursor/composer-2.5` (`redirect_mechanical_executor`). `cursor/grok-4.7` is roaming and stays. Use `contract=investigate` when the leg is facts, not edits.

**Model + lane (BINDING):**
- Omit `model=` resolves `cursor/grok-4.7` (`workflows.auto_judgment.model`). Omitted knobs follow that card. Pin `model=cursor/composer-2.5` when the leg is mechanical implement.
- Name `cdp/opus-5.5` in the directive when the leg needs a CDP bind. 2026-09-29: Fable credits near spent; no `cdp/fable` seat unless Kaywan asks. Hop successor model is `desired_model=cdp/opus-5.5-extra`.
- Pass `lane="B"` on the wire (`team_dispatch`) for every top-level generate, including read-only, plan, and bind-only. The checkout is a throwaway worktree. `lane=` is a **wire parameter**, not packet prose. `team_dispatch` top-level omit is 422 `lane_required`. `lane="A"` only on `CURSOR_LANE_B_SCOPE_REFUSED` or an explicit operator request for shared master. Bind-only and empty scope are not that exception.

Full recipe (mandatory conductor Use-line, six-block packet): `agent_skill:conductor` § **First-utterance spawn** — this skill does not duplicate it. Operator wire: `team_dispatch(op=generate, seat=cursor-sdk, lane=B, contract=conductor, …)`. The cursor-sdk seat nests further dispatches. `contract=implement` redirects the executor to Composer, so a conductor-class commission uses `contract=conductor`, on `team_dispatch`, not on `cursor_request`. Packet tier table: `conductor`.


This seat: frame the Question, ratify conductor Leg-boundary DISPOSITIONs, hold true operator-only gates — ¬ personally drive each nested admit/poll/harvest.

Seat-map branch: `cursor-sdk` is the executor for single-DIRECTIVE work; `cursor-sdk conductor=executor-of-executors` once framing closes.

**Exception:** UNFRAMED — Question still contested, or live architecture fork — stays on this seat until framing closes. A conductor cannot resolve what this seat has not decided to ask.

Evidence: agent-bus:7244 (cdp/opus DIRECTIVE loop, 8h44m then dead gap, IDE stand-down) vs agent-bus:7310 (conductor, 1h19m). Investigation: agent-bus:7359 / `cortex://notes/system/threads/7359-cdp-conductor-doctrine.md`.

## D5 — Mission negotiation

Before the mission is framed enough for D4, this seat and the cursor-sdk executor MAY negotiate shape headlessly — no live chat, no human mediation. The operator wire is ulg-code `team_dispatch`, not `agent_bus.request` / `cursor_request`. Same `TYPE: DIRECTIVE` / `contract: confer` envelope; add `negotiation_phase: proposal|counter|agree|ratify` plus closed 12-field set: negotiation_phase, negotiation_id, revision, in_reply_to_turn, proposal_hash, parent_thread, objective, scope, out_of_scope, acceptance, vision, idle_deadline (explicit `parent_thread:` as a body line, distinct from the tool argument `parent_thread`). `proposal_hash` = sha256: over sorted compact JSON of the six payload fields (parent_thread, objective, scope, out_of_scope, acceptance, vision). Auto replies `TYPE: DISPOSITION` with closed `negotiation.*` vocabulary.

Ordinary DIRECTIVEs without `negotiation_phase` are unaffected. Additive — not a replacement for attended charter-birth (`cortex://notes/system/playbooks/attended-charter-birth-with-cursor.md`) when a human is in chat.

Live field contract: full grammar in `cortex://notes/system/specs/directive-loop-mission-negotiation.md`; the tool **Mission negotiation** clause is the MCP discoverability mirror of the 12-field list above. Once `agree`/`ratify` closes, D4 takes over.

## Anti-patterns

| Bad | Good |
|---|---|
| `desired_model=auto` on a dense job | Pin `cursor/grok-4.7`, or `cursor/composer-2.5` when the leg is mechanical implement |
| `allow_long_body=true` on `agent_bus.request` | Rejected on `request`; `sidecar_content`; keep the ten §2 fields in `body` |
| cdp/opus drives a framed 5-G-row mission turn-by-turn over the DIRECTIVE loop | Commission a conductor (D4) once the Question is framed; adjudicate Legs, don't drive them |
| `lane="B"` only in packet prose | Wire `lane="B"` on `team_dispatch` |
| Restating conductor first-utterance spawn / negotiation field lists in `cdp-operator-proxy` | Point here; recipe of record stays on `agent_skill:conductor` § First-utterance spawn |
| Bolded "rule on this fork" AC with no `RULING` token (`contract: implement`) | `AC<n> — RULING:` then the fork. Turn 343 AC2 was a genuine withheld-lean judgment AC and still admitted mechanical |

## Pre-author checklist

- [ ] `TYPE: DIRECTIVE` first line; all ten D1 fields inline; `out-of-scope:` own line
- [ ] Judgment AC on `contract: implement` ⇒ line-start `RULING` / `AC<n> — RULING` (or density / `handoff:` / open-fork equivalent) — unmarked implement admits mechanical + Composer redirect
- [ ] Wire `contract` ∈ live enum; `vision:` on implement/investigate
- [ ] `summary` = ULG so-what ≤120; `desired_model` pinned when density is dense
- [ ] Mint-then-quote: every id in the body was read from a tool payload this turn
- [ ] Framed multi-step (≥3 G-row or bind-then-compose) ⇒ D4 conductor, not a G-row loop
- [ ] Pre-frame shape talk ⇒ D5 `negotiation_phase` on `contract: confer`, then D4
- [ ] Conductor: `contract=conductor` + omit `model=` + `model_knobs={"fast":"true"}` + wire `lane="B"` + `agent_skill:conductor` § First-utterance spawn (not a retired docstring token)

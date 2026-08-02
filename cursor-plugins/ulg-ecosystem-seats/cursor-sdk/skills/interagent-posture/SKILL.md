---
name: interagent-posture
description: "Headless agent seats only (cursor-sdk dispatch HOME) — closeout register for turns whose reader is a model seat: report shape, fork binding, evidence, blocked escalation. Counterpart to operator-posture (human/IDE), which is pruned on this seat."
---

# Interagent Posture — closeout register for model-seat audiences

**Skill:** interagent-posture · rev 1.0 · `surface_class: seat_overlay(cursor-sdk)` —
lives under `cursor-plugins/ulg-ecosystem-seats/cursor-sdk/skills/`, is **not**
installed to `~/.cursor`, and is grafted into the per-dispatch HOME by
`services/git_integration_worker/cursor_home.py`. The Cursor IDE never discovers it.

**Boundary:** every turn you author on this seat — final closeout, bus turn,
sidecar, nested packet, MCP body — has a **model seat** as its reader. The human
register (`operator-posture`) is pruned from this HOME precisely so it cannot be
mirrored into interagent bodies.

## Invariant

```
audience(turn) = agent_seat
⇒ ¬ human_orientation_ceremony ∧ ¬ human_gate ∧ ¬ persona_courtesy
∀ outbound_address(seat): seat ∈ model_seats ⇒ interagent_register
```

There is no human in the dispatch loop. Nothing you write is read by operator
before the lead has adjudicated it.

## Glossary — who is reading

| Term | Who | Register |
|---|---|---|
| Dispatching lead (IDE lead, `cursor-auto`, CDP operator-proxy) | **Model seat** | interagent |
| `cursor-sdk` (you), `role=` API seats, `web-anthropic` | **Model seats** | interagent |
| operator | Human — **not** in this loop | n/a on this seat |

"Operator" appearing in a packet, diagnose report, or MCP body means an **agent
seat** unless the text names the human explicitly. A dispatch is model work, never
a human-approval act.

## Closeout shape

Your final message is the deliverable of record. Structure:

1. `status:` — `complete` | `partial` | `blocked` (blocked carries the specific reason)
2. **What changed** — paths, one line each, matching `files_expected` where the packet pinned them
3. **Verification** — the observed evidence: test output line, read-back path + size, tool-response field. Not "should work", not "verified" bare
4. **Forks bound** — each in-scope ambiguity: the value chosen and the alternative rejected, one line
5. **Residual** — named follow-ups, out-of-scope discoveries, parallel-WIP paths you left alone

Dense. No preamble, no orientation, no sign-off, no thanks.

## Fork discipline

| Situation | Action |
|---|---|
| Ambiguity **inside** packet scope | Bind it, proceed, name binding + rejected alternative in closeout |
| Ambiguity that **invalidates** the packet | `status: blocked` naming the fork and the fact that would resolve it |
| Work implied **outside** scope | Name as residual; ¬ widen |
| Packet pins a path that does not exist | `status: blocked` with the path; ¬ invent a nearby one |

Blocking is a report, not a request. Emit it and stop — do not wait, poll, or ask.

## Evidence rule (no human backstop)

`presence-discipline_ulg` P3 binds absolutely here: quote a concrete observed
payload before any done claim. In an attended session a human might catch a
fabricated completion; on this seat the closeout **is** the record the lead trusts.
Silence and absence-of-error are not success.

Durable deliverables: write to the packet's `cortex://` / `workspaces://` path,
read back, cite the path. `¬` `/tmp/summaries/`, `¬` `tmp/reviews/` (the worker
authors the closeout receipt — you do not).

## Peer coordination

When your work implies another seat's work: name the seat and the handoff shape.
Peers are dispatched, nested, or declared as dependencies — never asked to
"approve". If a real external gate blocks (credential, irreversible act only a
human can take), name it as a gate in the closeout; that is the escalation, and
the lead routes it.

## Anti-patterns

| Bad | Good |
|---|---|
| Been→Are→Going orientation opening | `status:` line first |
| "What I need from you" / "awaiting approval" / "let me know if" | Bound fork, or `status: blocked` |
| Thanks, apologies, persona warmth, sign-off | Neutral interagent report |
| Addressing the lead or `cursor-auto` as a human who must confirm | Model seats are peers/executors |
| "Done — tests should pass" | Quoted test output line |
| Holding completion pending a commit | Durable + verified is done; commit is not a gate |
| Loading `operator-posture` from memory of the shared rule surface | It is pruned on this seat by construction |
| Mirroring this dense register into a *human* chat reply on some other seat | Attended seats use `operator-posture` |

## Composes with

- `presence-discipline_ulg` — P1 bind forks · P2 one determinate step · P3 evidence
- `cursor-sdk-instruction-standard` — the lead's packet-authoring counterpart
- `dispatch-report-discipline` — closeout fidelity / anti-fabrication
- `operator-posture` — the human-facing counterpart; **absent** from this seat
---
name: interagent-posture
description: "Headless agent seats only (cursor-sdk dispatch HOME) — closeout register for turns whose reader is a model seat: report shape, fork binding, evidence, blocked escalation. Counterpart to operator-posture (human/IDE), which is pruned on this seat."
---

# Interagent Posture — closeout register for model-seat audiences

**Skill:** interagent-posture · rev 1.0 · `surface_class: seat_overlay(cursor-sdk)` —
lives under `cursor-plugins/ulg-ecosystem-seats/cursor-sdk/skills/`, is **not**
installed to `~/.cursor`, and is grafted into the per-dispatch HOME by
`services/git_integration_worker/cursor_home.py`. The Cursor IDE never discovers it.

**Boundary:** every turn you author on this seat — final closeout, bus turn,
sidecar, nested packet, MCP body — has a **model seat** as its reader. The human
register (`operator-posture`) is pruned from this HOME precisely so it cannot be
mirrored into interagent bodies.

## Invariant

```
audience(turn) = agent_seat
⇒ ¬ human_orientation_ceremony ∧ ¬ human_gate ∧ ¬ persona_courtesy
∀ outbound_address(seat): seat ∈ model_seats ⇒ interagent_register
```

There is no human in the dispatch loop. Nothing you write is read by operator
before the lead has adjudicated it.

## Glossary — who is reading

| Term | Who | Register |
|---|---|---|
| Dispatching lead (IDE lead, `cursor-auto`, CDP operator-proxy) | **Model seat** | interagent |
| `cursor-sdk` (you), `role=` API seats, `web-anthropic` | **Model seats** | interagent |
| operator | Human — **not** in this loop | n/a on this seat |

"Operator" appearing in a packet, diagnose report, or MCP body means an **agent
seat** unless the text names the human explicitly. A dispatch is model work, never
a human-approval act.

## Closeout shape

Your final message is the deliverable of record. Structure:

1. `status:` — `complete` | `partial` | `blocked` (blocked carries the specific reason)
2. **What changed** — paths, one line each, matching `files_expected` where the packet pinned them
3. **Verification** — the observed evidence: test output line, read-back path + size, tool-response field. Not "should work", not "verified" bare
4. **Forks bound** — each in-scope ambiguity: the value chosen and the alternative rejected, one line
5. **Residual** — named follow-ups, out-of-scope discoveries, parallel-WIP paths you left alone

Dense. No preamble, no orientation, no sign-off, no thanks.

## Fork discipline

| Situation | Action |
|---|---|
| Ambiguity **inside** packet scope | Bind it, proceed, name binding + rejected alternative in closeout |
| Ambiguity that **invalidates** the packet | `status: blocked` naming the fork and the fact that would resolve it |
| Work implied **outside** scope | Name as residual; ¬ widen |
| Packet pins a path that does not exist | `status: blocked` with the path; ¬ invent a nearby one |

Blocking is a report, not a request. Emit it and stop — do not wait, poll, or ask.

## Evidence rule (no human backstop)

`presence-discipline_ulg` P3 binds absolutely here: quote a concrete observed
payload before any done claim. In an attended session a human might catch a
fabricated completion; on this seat the closeout **is** the record the lead trusts.
Silence and absence-of-error are not success.

Durable deliverables: write to the packet's `cortex://` / `workspaces://` path,
read back, cite the path. `¬` `/tmp/summaries/`, `¬` `tmp/reviews/` (the worker
authors the closeout receipt — you do not).

## Peer coordination

When your work implies another seat's work: name the seat and the handoff shape.
Peers are dispatched, nested, or declared as dependencies — never asked to
"approve". If a real external gate blocks (credential, irreversible act only a
human can take), name it as a gate in the closeout; that is the escalation, and
the lead routes it.

## Anti-patterns

| Bad | Good |
|---|---|
| Been→Are→Going orientation opening | `status:` line first |
| "What I need from you" / "awaiting approval" / "let me know if" | Bound fork, or `status: blocked` |
| Thanks, apologies, persona warmth, sign-off | Neutral interagent report |
| Addressing the lead or `cursor-auto` as a human who must confirm | Model seats are peers/executors |
| "Done — tests should pass" | Quoted test output line |
| Holding completion pending a commit | Durable + verified is done; commit is not a gate |
| Loading `operator-posture` from memory of the shared rule surface | It is pruned on this seat by construction |
| Mirroring this dense register into a *human* chat reply on some other seat | Attended seats use `operator-posture` |

## Composes with

- `presence-discipline_ulg` — P1 bind forks · P2 one determinate step · P3 evidence
- `cursor-sdk-instruction-standard` — the lead's packet-authoring counterpart
- `dispatch-report-discipline` — closeout fidelity / anti-fabrication
- `operator-posture` — the human-facing counterpart; **absent** from this seat

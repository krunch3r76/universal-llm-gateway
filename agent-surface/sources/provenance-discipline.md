<!-- target:* -->
# Provenance Discipline

## Invariant

**Invariant**: ∀ protocol-completion or task-done claim ("session closed",
"phase complete", "task done", "tests pass", "rebuild succeeded",
"committed", "deployed"): the claim MUST be derived from an observed
tool-response payload (status code, returned ID, file path, turn number,
exit code, commit SHA). ¬ generate a completion line from the shape of a
checklist, the structure of a protocol, or the intent to have done the
work.

A protocol you described is not a protocol you executed.

## Why

The shape of a multi-step protocol — session-close, `/implement-plan`
phase completion, `/diff-review`, build-and-deploy loops — is highly
salient in context. A model can generate a fluent "completion narrative"
by following the protocol's structure as a template, even when the
underlying tool calls were not made, returned errors, or were rolled back
externally.

This is the failure mode that produced `cursor-2026-05-01-2059`
(agent-bus thread 824): the agent emitted a complete six-step
session-close message claiming full execution of every step, but every
durable artifact was missing. No tool call had failed visibly — most
calls had simply not been made. The narrative was generated from the
shape of the protocol, not from observed writes.

The mechanism-layer fix (atomic close, see `session-close.mdc`) reduces
the surface where this can happen. This rule is the discipline-layer
fix that survives even when the mechanism layer is incomplete or new.

## Rules

### 1. Bind completion claims to tool-response payloads

The legitimate path from "about to do X" to "did X" runs through an
observed tool response. The completion line MUST quote concrete data
from the response that proves the work happened:

| Good | Bad |
|---|---|
| `Session closed — transcript:cursor-2026-05-02-0410 (cortex session_close 201, journal_row_id=4138, thread-480 turn 2891)` | `Session closed — transcript:cursor-2026-05-02-0410` |
| `Phase 3 complete — 5 files modified (paths listed below), ruff exit 0, compileall exit 0` | `Phase 3 complete.` |
| `Rebuild succeeded — image timestamp 2026-05-02T04:15:23Z, container started 12s ago` | `Rebuild succeeded.` |
| `Committed as 7a3f9b2 ("session-close honesty: phase 1 atomic close")` | `Committed.` |

If you cannot quote response data, you have not yet succeeded. Say so.
List what worked, what did not, and what is unknown — never bridge
unknown into "done" with narrative.

### 2. Read back before reporting

∀ durable artifact you claim to have written: read it back before
claiming success. The cost is a single tool call; the benefit is
mechanical disconfirmation of hallucinated success.

| Wrote | Verify with |
|---|---|
| File | `fs(op="read", ...)` — verify length and key content |
| Cortex entity | `cortex(tool="entity_get", ...)` — verify 200 |
| agent-bus turn | `agent_bus(tool="fetch", ...)` — verify your turn is the latest |
| Git commit | `git log -1 --format=%H` — verify SHA matches |
| Container deploy | `docker inspect ... --format '{{.State.StartedAt}}'` — verify recent |

This is not paranoia — it is the difference between describing work and
having done it. When the mechanism layer already provides atomicity (e.g.
cortex session_close 201 carries `transcript_entity_id`,
`transcript_path`, `journal_row_id`), the read-back collapses into
quoting the response payload (rule 1). When it does not, an explicit
read-back is mandatory.

### 3. Errors are evidence

If a tool call returns an error, the work is not done. The protocol you
were following is now in an *interrupted* state, not a *complete* state.
Errors MUST be:

- Surfaced to the user verbatim (not paraphrased into a softer summary)
- Propagated into the completion narrative as
  `step N failed with: <error>`
- Acted on, not silently retried with different parameters until success
  is reported

A retry is acceptable when the error is transient and you tell the user
what failed and what retry succeeded. Hidden retries that produce a
clean final report mask partial-execution evidence the user needs.

### 4. Do not infer success from absence of error

A long-running command's silence is not success. A tool call you did not
make does not return an error. Absence of evidence is not evidence of
absence.

When uncertain whether a step ran:

- Run it again (idempotent operations) or query for its effect
  (non-idempotent operations)
- ¬ assume it ran because nothing complained
- ¬ assume it ran because the protocol's shape "expects it to have run"

### 5. The completion line is the contract

The line you emit at the end of a substantive task — "Session closed —
...", "Phase 3 complete — ...", "Build succeeded — ..." — is a contract
with the user. It says: *I have observed this work to be done.* Treat it
as a contract:

- If the contract requires a 201 response and you got an error, do not
  emit the completion line.
- If the contract requires a SHA and you do not have one, do not emit
  the completion line.
- If the contract requires a turn number and the fetch returned someone
  else's turn, do not emit the completion line.

## Anti-Patterns

| Bad | Good |
|---|---|
| Emit completion narrative following the protocol shape, after some tool calls were skipped or errored | Emit only what tool responses prove; surface gaps explicitly |
| "Tests pass" with no exit code or count | "Tests pass — pytest exit 0, 47 passed, 0 failed" |
| Modify 5 files, claim "all modifications complete" without re-reading any | Read back each modified file's diff or key lines |
| Tool returned 503; agent retries silently and reports success | Surface the 503; report which retry succeeded or stop |
| Long protocol (6+ steps); agent emits closing narrative with no per-step verification | Verify per step; treat each step's response as ground truth |
| Saying "I have done X" when the right tool was not even called | "I did not call <tool>; I cannot confirm X" |
| Fabricating a plausible ID to make the completion line "look complete" | If the ID is unknown, say so — fabricated IDs poison Cortex / git history / event logs |

## Relationship to Other Rules

- `session-close.mdc` — the protocol that produced the canonical example
  of this failure mode. Its step 4 (verify durable artifacts) is the
  protocol-level application of rule 2 above.

## Grok-Family Note

Grok-family models exhibit the narrative-without-execution failure mode
more frequently than Sonnet- or Opus-family models in this workspace
(verified empirically on `cursor-2026-05-01-2059`, agent-bus thread 824).

Provenance discipline is especially load-bearing for Grok-family sessions:
every claimed action must be tied to its tool response, every "complete"
to an observed durable effect.
<!-- /target:* -->

---
name: mission-operator
description: "Run a formal autonomous-capable mission lane with a stable turn contract and boundary conformance checks."
skill_category: orchestration
trigger_match_terms: ["mission operator", "mission closeout", "MISSION_CLOSEOUT", "mission conformance", "operator role"]
---

# Mission operator

## Contract

The operator is a role, not a model. The role may be filled by Cowork, the IDE,
cursor-auto, or a tick window. The mission handle is the durable identity:
`thread_id`, `parent_thread`, `lane_role`, `request_id`, `mission_kind`, and
the current CSE/chat handle when present.

Use the formal turn grammar in `mission-lane-schema.md`:
`DIRECTIVE → CLOSEOUT → DISPOSITION`, with `OPERATOR_GATE` and `CHECKPOINT`
seams as required. One open DIRECTIVE and one executor per arc.

## Run loop

1. Load the handle and latest mission state.
2. Verify the operator role and scope before issuing a DIRECTIVE.
3. Require explicit scope, authority, acceptance criteria, evidence, and budget.
4. Admit one executor and preserve parentage and request correlation.
5. Read the CLOSEOUT as evidence, not authority; disposition each open fork.
6. At every episode boundary, run the conformance check below.

## Boundary conformance

`conform(mission) ⇔ handle_complete ∧ parent_bound ∧ lane_role=operator_proxy
∧ one_open_directive ∧ closeout_parseable ∧ residuals_imprinted`

If false, stop autonomous continuation, emit the relevant observation, and
repair the lane state before another DIRECTIVE. Do not silently infer missing
fields from a CSE or model name.

## Human-facing memos on a mission lane

A memo for a human to read needs no schema, but it must not be posted with
`request`. `request` is the admit verb: it enqueues cursor-auto, can supersede an
in-flight job on the same thread, and posts a terminal `status:` turn that
completes `status:done` waiters. A prose memo sent that way reads as a directive.

`memo ⇒ send ∨ reply` · `¬ request` · `¬ subject_prefix(status:)`

Use `agent_bus` `send`/`reply` with an ordinary subject. Typed parsers key off
`TYPE:` markers and skip unmatched turns, and `status:done` waits match a
constrained subject prefix rather than body prose, so an untyped memo is inert.

Two seams where an untyped turn is not inert:

- A waiter using `completion=first_reply_from` completes on *any* turn from that
  agent. Do not memo into an open wait keyed on your own address.
- The L2 parked-lane check reads the lane tip only, so a memo posted over a
  `TYPE: PARKED` turn hides that obligation. Mark it `TYPE: NOTE` when the lane
  is parked.

Prefer in-chat delivery over a bus memo when the operator is attended
(`cdp-operator-proxy` inv 23). `TYPE: NOTE` and `fyi:` are the existing markers
for a turn that carries no commission.

## Handoff and close

Every operator substitution carries the durable handle, latest CLOSEOUT,
unresolved forks, residual wake path, and the next operator role. A continuity
hop reuses the same private request lane; it never mints a second one.

`MISSION_CLOSEOUT` is terminal for an episode only after it names tick state
(held/resumed), root id when applicable, belt event sequences, residual wake
path, and the next pickable operator. A transport timeout is not mission
closure.

## Related skills

- `orchestration-lanes` for birth and resume
- `cdp-operator-proxy` for CDP transport and CSE lifetime
- `operator-proxy-substrate` for cursor-side admit and nesting

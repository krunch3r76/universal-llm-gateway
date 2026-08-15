---
name: orchestration-lanes
description: "Establish and resume informal continuity roots or formal autonomous missions without confusing their schemas."
skill_category: orchestration
trigger_match_terms: ["orchestration lane", "continuity root", "mission lane", "session conversion", "operator_proxy"]
---

# Orchestration lanes

## Kinds

`root` is informal continuity: a durable address for a long session across
context windows. `mission` is formal and autonomous-capable: an operator or
tick can run it unattended against a machine-readable contract.

The store's `spine ∈ {root, work}` is an implementation detail. A mission is
identified by its handle and parent association, never inferred from spine.
`orchestrator_continuity` and `tick_charter` are root CHECKPOINT profiles.

## Root birth

1. Bind one objective sentence describing the topic being continued.
2. Mint a continuity document or charter pointer.
3. Create the root thread and post a birth CHECKPOINT indexing the document and
   a concrete `Next-pickup`.
4. Stamp `role:root`; enroll `charter-runner` only when machine ticks are wanted.

Do not invert this order. Use `checkpoint-discipline` for the CHECKPOINT schema.
Session close still records transcript, journal, and edges; the root replaces
the handoff carrier, not provenance.

## Mission birth

Missions are born as children; never promote a root in place:

`mission(t) ⇒ born_as_child(t) ∧ parent_thread(t) ∧ lane_role(t)=operator_proxy`

Create a fresh work thread with `parent_thread=<root-or-parent>` and
`lane_role=operator_proxy`, then persist the mission handle before dispatching
the first operator turn. `parent_thread` and `lane_role` are both-or-neither.
Use `mission-operator` for every subsequent turn.

## Resume

1. Read the durable handle and verify `thread_id`, parent, lane role, request
   id, mission kind, and CSE/chat handle as applicable.
2. Read the latest root CHECKPOINT for continuity, or the mission schema state
   and latest CLOSEOUT for a mission.
3. Designate the current operator by role, not model name. Cowork, IDE, and a
   tick window may fill the role.
4. Continue the existing thread. Never create a second private request lane
   for a CSE continuity hop.

## Boundaries

The CDP mission CSE is a Chrome host, not a bus thread. Executor children are
work lanes under a mission, not a third kind. `purpose=mission` configures the
CDP episode; it does not replace lane birth or parentage.

## Related skills

- `agent-bus-discipline`
- `checkpoint-discipline`
- `mission-operator`

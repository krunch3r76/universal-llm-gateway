---
trigger_match_terms: ["agent-bus-multitask", "agent_bus_multitask", "multi-thread", "agent-bus", "batch", "multitask", "dispatch-delegation", "subagent", "decomposition", "batches", "mode", "parallel"]
description: Subagent decomposition for multi-thread agent-bus batches in Multitask Mode — parallel vs sequential agents, tier assignment, compact handoffs.
---

# Agent-Bus Multitask Decomposition

## Decision rule

```text
threads_independent(A,B) ⇒ parallel_sibling_agents(A,B)
depends_on(B,A) ∧ verbose_intermediate(A) ⇒ sequential_separate_agents(A→B)
depends_on(B,A) ∧ ¬verbose_intermediate(A) ∧ chain_length≤2 ⇒ monolithic_agent_ok
```

`verbose_intermediate(A)` = acceptance gates, multi-step merges, PR workflows, tool-call loops, or >1 paragraph of working state B does not need.

| Structure | Pattern |
|---|---|
| no shared state/routing | parallel siblings |
| A→B + verbose A | separate agents; compact handoff |
| A→B + trivial A status | monolithic |
| 3+ mixed dependencies | parallel independent tracks; sequential within chains |

## Routing notation

`1016 -> 1017, 1009` means:
1. Resolve 1016 first.
2. Extract compact payload `{status, evidence, key_outputs}` ≤1 paragraph.
3. Post payload to 1017 and 1009.
4. Process 1017 after the gate.

`∀ routing_annotation: sequential_across_gate ∧ ¬parallel_across_gate`.

## Sequential separate-agent pattern

For `A→B`:
1. Agent A gets full A context + boot; executes A; returns compact result.
2. Parent extracts `{status, evidence, key_decision}` into one-paragraph handoff.
3. Agent B gets B context + handoff only; no A tool logs/working noise.

`handoff_state_size > saved_context_overhead ⇒ keep_monolithic`.

## Tier assignment

`∀ subagent: assign minimum sufficient tier`. Separate agents permit per-step tier selection; monoliths inherit parent tier.

| Step type | Suggested tier |
|---|---|
| acknowledgments, routing posts, thread-state writes | Grok 4.20 / Sonnet low, thinking off |
| mechanical sequences: merges, ruff, lifecycle | Grok 4.20 / Sonnet medium, thinking off |
| acceptance gates / evidence verification | Sonnet 4.6 high, thinking on |
| one-subsystem debugging/root cause | Sonnet 4.6 high, thinking on |
| clear green-lit PR execution | Sonnet 4.6 medium, thinking on |
| cross-agent protocol, rule changes, architecture | Opus low, thinking on |

Escalate at natural pause points per `judgment-escalation-ladder` when a fork remains after recon.

## Boot overhead

`separate_subagent ⇒ cortex_brief (~1–2s)`. Negligible for substantive work; non-negligible for trivial acknowledgments. Bundle trivial turns.

## Anti-patterns

| Bad | Good |
|---|---|
| Monolith where each step emits verbose working state | sequential separate agents |
| Parallel agents across routing dependency | sequential gate then fan-out payload |
| Parallel agents for ack-only turns | bundle acknowledgments |
| Split when handoff state exceeds working-state savings | keep monolithic |
| Launch B with A logs/tool traces | reduce A to compact payload first |

## Load with

- `agent-bus-discipline` before composing bus turn bodies.
- `dispatch-workflow` for model-string/executor selection.
- `implementation-plan-workflow` when batch belongs to a multi-phase plan.

## Failure-targeting example

Request: `/agent-bus 1016, 1017, 1009, 1015, 1016 -> 1017, 1009`.

Classification: 1015 independent; 1016 gate; 1017 gated by 1016; 1009 receives 1016 routing update.

Correct decomposition: launch A=1015 and B=1016 in parallel; B routes compact result to 1009; launch C=1017 only after B handoff is available.

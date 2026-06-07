---
name: agent-bus-multitask
description: Subagent decomposition for multi-thread agent-bus batches in Multitask Mode — parallel vs sequential agents, tier assignment, compact handoffs.
---

# Agent-Bus Multitask Decomposition

## Core Decision Rule

```
threads_independent(A, B) ⇒ parallel_sibling_agents(A, B)

depends_on(B, A) ∧ verbose_intermediate(A) ⇒ sequential_separate_agents(A → B)

depends_on(B, A) ∧ ¬verbose_intermediate(A) ∧ chain_length ≤ 2 ⇒ monolithic_agent_ok
```

`verbose_intermediate(A)` is true when A involves substantive execution: acceptance gates, multi-step merges, PR workflows, tool-call loops, anything producing >1 paragraph of working state that B does not need.

## Decision Table

| Thread structure | Pattern | Why |
|---|---|---|
| Threads share no state, no routing between them | **Parallel sibling agents** | Reduce wall-clock time; each agent gets a clean context |
| A → B (hard dependency) + A produces verbose output | **Sequential separate agents** | B only needs a compact result from A; keeping them separate prevents A's working noise from polluting B's context |
| A → B + A produces a trivial status (flag, one-liner) | **Monolithic agent** | Handoff state is small; coordination overhead > benefit |
| 3+ threads, mixed dependency/independence | **Hybrid**: parallel for independent tracks; sequential separate within each dependent chain | Apply both rules at their respective boundaries |

## Routing Notation: `A -> B, C`

`1016 -> 1017, 1009` means:
1. Resolve thread 1016 first (it is the gate)
2. Extract its compact result: `{status, evidence, key_outputs}` — one paragraph max
3. Post that compact result as a turn in thread 1017 and thread 1009 to unblock/update them
4. Then process thread 1017 (which may now proceed past its gate)

∀ routing annotation: always sequential, never parallel across the gate boundary.

## Sequential Separate Agent Pattern

For a dependency chain A → B:

**Agent A prompt**: includes full thread context, cortex boot, execute A's work → return compact result.

**Parent (foreground coordinator)**: receives Agent A's output, extracts the handoff payload (status + evidence + key decision), constructs a one-paragraph handoff summary.

**Agent B prompt**: seeded with handoff summary + thread B context → execute B's work with clean context.

The handoff payload is small. Agent B's context starts clean — no Agent A's tool calls, logs, or working state.

## Tier Assignment per Subagent

∀ subagent in a decomposed batch: assign the minimum tier sufficient for its task class.
Separate agents enable per-step tier selection — a key advantage over monolithic agents,
which inherit the parent's tier for all steps regardless of what each step actually needs.

| Step type | Family | Effort | Thinking |
|---|---|---|---|
| Acknowledgment-only turns, thread state writes, routing posts | Grok 4.20 / Sonnet | Low | off |
| Mechanical sequence (merge steps, ruff passes, service lifecycle) | Grok 4.20 / Sonnet | Medium | off |
| Acceptance gate / verification with evidence | Sonnet 4.6 | High | on |
| Debugging / root-cause within one subsystem | Sonnet 4.6 | High | on |
| PR execution following a clear green-light | Sonnet 4.6 | Medium | on |
| Cross-agent protocol work, rule changes, architectural assessment | Opus 4.7 | Low | on |

Per `model-tier-awareness.mdc`: escalate when triggers fire, downgrade at natural pause points.
For cross-agent protocol scope (session-close protocol, shared cortex infrastructure): NOT SUITABLE
at Sonnet — escalate to Opus regardless of edit size.

**Cost implication**: an acceptance gate (Sonnet High) followed by a 13-step merge sequence
(Grok/Sonnet Medium) run as separate agents costs less than both steps at Sonnet High in one
monolithic agent — without sacrificing quality on either step.

## Cortex Boot Overhead

∀ separate subagent: re-runs cortex boot (one MCP call, ~1-2s).

This overhead is **negligible** for steps that do substantive work (acceptance gates, multi-step merges, 13-step sequences). It is non-negligible only for trivial acknowledgment-only turns — bundle those into a single agent to avoid redundant boot calls.

## Anti-Patterns

| Bad | Good |
|---|---|
| One monolithic agent for a chain where each step produces verbose working state | Sequential separate agents with compact handoffs |
| Parallel agents for threads with routing dependencies between them | Sequential agents; routing notation is always sequential |
| Parallel agents for acknowledgment-only turns where cortex boot cost > benefit | Bundle acknowledgments into one agent |
| Splitting into separate agents when the handoff state is larger than the working state | Keep monolithic when the intermediate result is > the saved context overhead |
| Forgetting to extract a compact handoff from Agent A before launching Agent B | Always reduce Agent A's output to `{status, evidence, key_outputs}` before seeding B |

## Related cortex skills

- `cortex:agent-skills/agent-bus-discipline.md` — mechanics of `agent_bus` ops (new-thread vs reply, sidecar body pattern, thread lifecycle, large-payload navigation). Load before composing turn bodies inside the subagents this skill dispatches.
- `cortex:agent-skills/dispatch-workflow.md` — model-string convention and executor selection by task shape. The tier assignment table above mirrors its density-tier guidance.
- `cortex:agent-skills/implementation-plan-workflow.md` — when a thread batch is one phase of a multi-phase plan, the deck structure and coordinator-mode dispatch loop live here.

## Applied Example (from session)

**Request**: `/agent-bus 1016, 1017, 1009, 1015, 1016 -> 1017, 1009`

**Analysis**:
- 1015 ("green-light PR 1") — fully independent of 1016/1017/1009 chain
- 1016 ("acceptance gate") — must complete before 1017
- 1017 ("13-step merge", GATED on 1016) — depends on 1016's result
- 1009 ("proceed as designed") — receives routing from 1016

**Optimal decomposition**:

| Agent | Threads | Pattern |
|---|---|---|
| A (launch immediately) | 1015 | Parallel — fully independent |
| B (launch immediately) | 1016, then route result to 1009 | Sequential separate step 1 |
| C (launch after B returns) | 1017 (seeded with 1016 result) | Sequential separate step 2 |
| B also handles | 1009 update | Bundled into B (trivial acknowledgment post-routing) |

A and B run in parallel. C starts only after B's handoff payload is available.

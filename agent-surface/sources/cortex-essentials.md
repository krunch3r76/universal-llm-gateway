<!-- target:* -->
# Cortex Essentials

Cortex is shared knowledge graph: **entities** (typed nodes), **assertions** (epistemic claims with confidence), **relationships** (structural links), **session edges** (reasoning connections), **journals** (episodic memory).

**Invariant**: ∀ claims about known entities or decisions: search Cortex first. ¬assert without evidence. Assertions are grounded facts, not vibes.

## Calling Convention

All Cortex CRUD via `cortex(tool=..., arguments='...')`. Arguments is a JSON string.

In Cursor: `CallMcpTool(server="user-vortex", toolName="cortex", arguments={...})`

## Session continuity brief

```
cortex_brief(agent="cursor")  # slim briefing card; returns session_id + sections_available
cortex_brief(agent="cursor", transcript_id="cursor-2026-04-07-0818")  # continuation brief
```

Hold `session_id` from `cortex_brief` for the full session — pass to `supersede`, `edge_create`, `relationship_create`.
`cortex_brief` is a continuity briefing card + session_id mint — not a mode switch into Cortex.

## 4 Canonical Ops

```
# Fetch entity with assertions (add include_edges: true for reasoning connections)
cortex(tool="entity_get", arguments='{"entity_id": "decision:my-slug"}')

# Hybrid search (FTS5 + vector, CombMAX scoring)
cortex(tool="search", arguments='{"query": "UDS transport invariant", "limit": 10}')

# Seed an assertion
cortex(tool="assert", arguments='{
  "entity_id": "decision:my-slug",
  "claim": "what was decided and why, including the alternative rejected",
  "confidence": "confirmed",
  "evidence": "source context",
  "evidence_uris": ["agent-bus:032"],
  "derivation_type": "compression",
  "session_id": "cursor-YYYY-MM-DD-HHmm",
  "agent": "cursor"
}')

# Read recent journals
cortex(tool="journal_read", arguments='{"limit": 3}')
```

## Confidence

| Level | Meaning |
|---|---|
| `confirmed` | Verified fact or settled decision |
| `believed` | Working assumption, high confidence |
| `suspected` | Pattern-based inference, not yet verified |
| `hypothesized` | Theory under investigation |

## When NOT to assert (channel routing)

`assert` is the most prominent surface in the cortex tool taxonomy and the path
of least resistance for "leaving a trace." That makes it a magnet for content
that should land elsewhere. Run channel-routing **before** the confidence
decision, not after.

**Bias**: under-assert by default. False negatives cost one missing search hit;
false positives compound (entrenchment, semantic-search ranking, mislead future
agents who treat entity-attached assertions as current truth).

### Three gates — ∀ assert call: pass all three or do not assert

| Gate | Question | Fail ⟹ |
|---|---|---|
| **Stranger** | Would a stranger 30 days from now, querying the entity, benefit from this claim? | Journal it |
| **Negation** | Could this claim be wrong, superseded, or irrelevant in 24 hours? | Don't write it; the state is transient |
| **Subject-centered** | Does the claim add something to the entity that doesn't follow trivially from `workflow_state`/`status`? | Tautology; skip |

### Channel ladder

| Content | Right channel |
|---|---|
| Durable empirical finding (cost curves, behavior under load, reproducible measurement) | `assert` |
| Architectural principle, contract, invariant | `assert` |
| Bug fix record / "I made this change" | git commit body |
| Session work narrative ("did A then B then C") | `journal_write` |
| Smoke-test pass | nothing — silence is the record |
| Smoke-test fail surfacing a real defect | `friction(...)`, then fix |
| "I caught my own mistake mid-session" | git commit + docstring caveat at the source of the trap |
| Plan / next-step intent | `entity_create` (todo) or in-context todo list — not an assertion |
| Multi-todo arc / grouping | `task:` container + `child_of` leaf `todo:` entities + spec (`task-seed`/`task-close` workflows retired 2026-06-04; entity type live) |
| Ranked open todos | `cortex(tool="todo_candidates", ...)` |

### When you've already seeded noise: retract, don't supersede

If you catch yourself having seeded an assertion that fails the three gates,
the right move is **retraction** (e.g. `assertion_update` setting
`valid_until` to now, or removal). Writing a *new* assertion to "correct"
the noisy one compounds the noise — now two assertions describe one
transient state. The entity ends up cleaner with no record than with the
asserted-then-superseded pair.

## Subgraph Materialization

For session-open canvas: `cortex(tool="render_subgraph", arguments='{"root": "project:slug", "hops": 1, "top_k_assertions": 10}')`. Size gate ≤40KB; compaction path if exceeded. Full guidance: Use the `cortex-orientation` skill.

## Deep Reference

Full CRUD, todos, write-safety, session-close protocol, activity journal, session edges:
load `cortex-deep-ref.mdc` on demand. Required before any session close.
<!-- /target:* -->

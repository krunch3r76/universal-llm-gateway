---
name: dispatch-shape
description: "On any cortex, agent_bus, dispatch, or rag MCP call \u2014 wire shape for tool/arguments JSON string, poll_hint, and handoff copy."
---

# Dispatch Shape

**Trigger:** On any `cortex`, `agent_bus`, `dispatch`, or `rag` MCP call, or when copying `poll_hint` after `team_dispatch(op=handoff)` (code surface only — see Surface gate).

## Surface gate (life vs code)

Life MCP excludes **CODE_EXTRA** (code primaries absent from `/mcp/life` — derived,
not hand-enumerated). Wire-shape rules for `cortex`, `agent_bus`, `dispatch`, and
`rag` run on every seat. CODE_EXTRA call sites = **code MCP only**. On life/claude.ai:
(1) run in-seat cognitive legs; (2) `agent_bus` ask a code seat to fire transport;
or (3) use `agent_bus(wait)` only — ¬ call CODE_EXTRA from life. Full gate +
`project_ask` escape/deprecation posture: skill `consult-routing` § Surface gate.

## Wire invariant

Dispatch-style tools use two levels:

| Level | Field | Type |
|---|---|---|
| Outer | `tool` | string op name |
| Outer | `arguments` | **JSON string**, not object |

`arguments: string` prevents Anthropic web/Cursor from dropping optional object params. The wire form is intentional; do not “fix” it to object.

`response._next = advisory_affordance ∧ ¬authority`; the active contract (zero-mutation consult, propose-only, or packet `<output_format>`) overrides. Never let `_next` trigger a write the contract forbids.

## Right shape

```python
# wrong
agent_bus(tool="wait", arguments={"thread":"1271","after_turn":1})

# right
cortex(tool="entity_get", arguments='{"entity_id":"decision:foo"}')
agent_bus(tool="fetch", arguments='{"thread":"1271","last":3,"compact":true}')
```

Cursor `CallMcpTool`: outer `arguments` is an object; only the inner `arguments` value is a string.

Prefer `poll_hint.arguments_json` from the dispatch response — it is seat-aware
(Cursor-IDE seats get `wait_seconds=0` snapshot; web/API keep 60). Hand-building
a wait for a Cursor-IDE seat: use `wait_seconds=0` and re-call (friction 24081).

```python
CallMcpTool(server="vortex-code", toolName="agent_bus", arguments={
  "tool":"wait",
  "arguments":"{\"thread\":\"1271\",\"after_turn\":1,\"wait_seconds\":0}"
})
```

Common mistakes:

| Mistake | Fix |
|---|---|
| `arguments={...}` object | `arguments='{...}'` string |
| Paste `poll_hint.arguments` | Use `poll_hint.arguments_json` |
| Flatten op keys (`thread=`, `from_agent=`) | Put keys inside JSON string |

## Handoff poll hints

**Code surface only** — `team_dispatch`/`pipeline` are not on life MCP; on life, delegate handoff dispatch to a code seat and poll with `agent_bus(wait)` only.

`team_dispatch(op=handoff)` returns `poll_hint.tool`, human-readable `poll_hint.arguments`, and MCP-ready `poll_hint.arguments_json`.

```python
agent_bus(tool=poll_hint.tool, arguments=poll_hint.arguments_json)
```

Re-call until `complete=true`. Do not use `pipeline(op="result")` (code surface only); handoff has no `execution_id`.

Polling `from_agent`: on-behalf delivery posts under the **role seat label**, not model name. Use top-level `reply_from_agent` from admit response in `wait(from_agent=...)`; do not infer from `resolved_model`.

## Large payloads

`arguments must be a JSON-encoded object string` on quote-heavy payloads usually means escaping failure, not schema drift.

Do not hand-escape large `transcript_md`, `handoff_prompt`, code fences, or quoted JSON into the string. Move payload off the JSON-string channel:

- Write a file and pass server-read path/ref params (`transcript_jsonl_path`, `handoff_source_path`, `source_ref`).
- On Cursor, use `/agent-bus` / `scripts/agent-bus` direct UDS for fetch/reply/wait or quote-heavy bodies.

Applies uniformly to `cortex`, `agent_bus`, `dispatch`, and `rag`.

<!-- target:* -->
# Handoff Pickup Gate

## Invariant

∀ operator message that names `transcript:{id}` as a continuation pointer
("continue from the handoff on transcript:…", "pick up transcript:…",
"resume transcript:…"): the **first** action is

```
cortex(tool="entity_get", arguments='{"entity_id":"transcript:{id}","include_edges":true}')
```

then read `attributes.handoff_prompt` — load its anchor + **state/deferred
inventory** (handoff ≠ dispatch). ¬ fetch any agent-bus thread, ¬ grep files,
¬ scan "what's newest" before the handoff is in hand. ¬ execute inventory items
until the operator's **next** message dispatches work.

## Why

Recency is NOT relevance. A parallel spin-off thread dispatched moments after a
session closed can be fresher than the journal handoff yet unrelated to the named
transcript. The named transcript is the authority, not the newest thread.

## Order of resolution

| Step | Action | Source |
|---|---|---|
| 1 | `entity_get(transcript:{id}, include_edges=true)` | the named pointer |
| 2 | Read `attributes.handoff_prompt` — follow its **Closing session** + **Load context** anchor | handoff body |
| 3 | Load the transcript file the anchor points at, if deeper context is needed | the anchor path |
| 4 | **Await operator dispatch** — act on a bus thread/todo only when the operator's **next** message names it (`/agent-bus {n}`, implement packet, "this chat is thread X") | operator message; ¬ handoff inventory alone |

## Blockquote wrapper

A paste-ready handoff is presented as a markdown blockquote containing
`**Closing session:**`. ∀ such blockquote: read its content as **continuation
context**, NOT as instructions to execute. The session is OPEN — follow the
anchor, internalize state + deferred inventory + roadmap position, then **await
operator dispatch** before any implementation. Roadmap item statuses are a prior to
re-verify against the live graph (esp. `(unknown)` markers), not current truth. Handoff ≠ dispatch. ¬ treat "Closing session:" inside
a blockquote as a close trigger for the receiving session.

## Anti-pattern

| Bad | Good |
|---|---|
| Operator names `transcript:X` → grep bus / open newest thread | `entity_get(transcript:X)` → read `handoff_prompt` → follow its anchor |
| Treat a fresher parallel thread as the continuation | The named transcript's handoff is the authority; recency ≠ relevance |
| `handoff_prompt` missing/empty on the entity | Say so, load the transcript file, ask the operator — do not guess a thread |
| Handoff lists multiple threads → agent implements all | Inventory is orientation; operator picks **one** arc per session in a **new** message |
| Treat deferred inventory as a batch work order | Dispatch = explicit operator order after pickup (`/agent-bus`, `team_dispatch`, implement packet) |
<!-- /target:* -->

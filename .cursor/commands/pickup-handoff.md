Pick up a continuation handoff from a named closing session. Use when the operator
says "continue from transcript:{id}", "pick up the handoff on {id}", or pastes a
continuation pointer.

**Load**: `@handoff-pickup_ws` (always-applied; this command is its explicit form)

## Argument forms

```
/pickup-handoff transcript:cursor-YYYY-MM-DD-HHMM
/pickup-handoff cursor-YYYY-MM-DD-HHMM        # transcript: prefix added automatically
```

## Instructions

### Step 1: Resolve the transcript entity FIRST
Normalize the argument to `transcript:{id}` (add the prefix if absent), then:

```
cortex(tool="entity_get", arguments='{"entity_id":"transcript:{id}","include_edges":true}')
```

Do NOT fetch any agent-bus thread, grep files, or scan recent activity before this
call. The named transcript is the authority (incident cursor-2026-06-06-2222: a
pickup that skipped this landed on a fresher parallel thread instead).

### Step 2: Read the handoff
From the entity's `attributes.handoff_prompt`:
- Follow the **Closing session** + **Load context** anchor.
- If the handoff is missing/empty, load the transcript file
  (`fs(cortex, op=read, path=notes/system/transcripts/{id}.md)`) and, if still
  ambiguous, ask the operator which arc to resume — do not guess.

### Step 3: Load deeper context only if needed
If the handoff prose is insufficient, read the transcript file the anchor points
at. The structural layer (Decisions / Files modified / Open items) orients you.

### Step 4: Act on outstanding work
Only after the handoff is in hand, follow its § Outstanding work / § Next steps
(bus threads, todos, poll hints) — in the order the handoff specifies, not by
recency.

## Relationship to the close side
The closing session is server-enforced to include the anchor
(`handoff.missing_transcript_anchor` 422 — see `/session-end` Step 6b). This
command is the matched pickup half: the close guarantees the anchor exists; this
guarantees the next session reads it first.

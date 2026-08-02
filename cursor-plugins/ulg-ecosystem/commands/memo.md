Summarize the current chat session as a structured memo to Claude (ack only, no action).

## Purpose

Generate a concise handoff memo from the current conversation that Web Claude
can acknowledge and optionally persist (journal, Cortex, or neither). The memo
is **informational only** — it does not request action.

## When to Use

Invoke at the end of a chat session (or referring to prior chats) when work was
done that other Claude instances should know about. Skip for trivial/no-op sessions.

## Instructions

### 1. Synthesize the Conversation

Read the current conversation (and any prior chats the user references).
Classify all work into exactly two domains:

**Domain 1: Feature-change** — what was added, solved, or unblocked
- New/changed REST endpoints, query params, response shapes
- New pipeline primitives, config knobs, step semantics
- Previously failing workflows now working
- New observability (events, signals, viewer outputs)
- New rules, lessons, or conventions established
- Architecture decisions made (with rejected alternatives noted)

**Domain 2: Investigation** — what was learned but did not ship, or no-op
- Root causes identified but not yet fixed
- Approaches explored and abandoned (and why)
- Discussions that clarified intent without producing code changes
- Policy/convention discussions not yet codified

If Domain 2 is empty (everything shipped), state: "No investigation-only items."

### 2. Gather Evidence

Collect from the conversation:
- Key files touched (source paths only, no `tmp/`)
- Key identifiers (endpoint paths, pipeline IDs, event signals, config fields)
- Prior chat references if the user provided them

### 3. Format the Memo

Output the memo in this exact format (the user will paste it to Web Claude):

```markdown
## Memo to Claude: {concise title for the work}

**Project:** {workspace name, e.g. universal-llm-gateway}
**Date:** {today's date}
**Source:** Cursor chat session{' + prior chats' if referenced}

> This memo is for acknowledgment only. Do not take action.
> If worth preserving, decide whether to store in journal and/or Cortex.

### What changed (feature-change)
- {bullet: what was added/changed — be specific about surfaces}
- {bullet: what was solved/unblocked}
- {bullet: developer-visible impact}

### What was learned (investigation)
- {bullet: key learning or "No investigation-only items."}

### Key files / surfaces
- {file or endpoint path}
- {file or endpoint path}

### Prior context
- {reference to prior chat if any, or "None"}
```

### 4. Present to User

Output the formatted memo. Do not post it anywhere — the user will copy it.

## Rules

- The agent synthesizes from conversation context — do not ask the user to fill in fields
- Keep bullets concise (1 line each, action-oriented language)
- "What changed" bullets should answer: "what would a Claude developer do differently now?"
- Do not include implementation details unless they are the feature (e.g. a new config field name)
- Do not request action from Web Claude — the memo is ack-only
- If the user references prior chats, read the transcript if available, otherwise note "referenced but not loaded"
- ¬ include `tmp/` paths in key files

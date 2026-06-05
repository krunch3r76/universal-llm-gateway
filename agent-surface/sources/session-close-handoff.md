<!-- target:* -->
# Session-close continuation handoff

Load when the operator requests a handoff on close (`with handoff`, `continuation handoff`, poll-delegation to a fresh session).

Companion: `session-close-handoff-depth-gate.md` (depth ∈ {light, verbatim}); kernel `agent-skills/session-close-kernel.md` § Step 6b.

## Invariant — transcript anchor (MANDATORY)

∀ `handoff_prompt` on `session_close`: the text MUST tell the **next** session how to load **this** closing session's transcript — not only the outstanding task (bus thread, todo, poll loop).

Boot does **not** auto-surface handoffs (assertion 8384). Without an explicit anchor, the next agent starts cold even though `transcript:{session_id}` exists.

**Required lines** (substitute `{session_id}` = closing session id, `{agent}` = seat slug):

```markdown
**Closing session:** `transcript:{session_id}`
**Load context:** `fs(cortex, op=read, path=notes/system/transcripts/{session_id}.md)`
  — or `cortex_boot(agent="{agent}", transcript_id="{session_id}")`
```

Then § Outstanding work and § Next steps (poll hints, bus threads, todos).

## Depth

| Operator signal | `transcript_depth` |
|---|---|
| Handoff requested | **`light`** minimum (never `none`) |
| Ceremonious / full walk-back | `verbatim` |

## Verified alternative (`handoff_source_path`)

Prefer file-backed derivation when the handoff body is long:

1. Append to `session_summary_md` (or a sidecar) a `## Handoff` section wrapped in:

   `<!-- handoff:continuation:start -->` … `<!-- handoff:continuation:end -->`

2. Pass `handoff_source_path=notes/system/transcripts/{session_id}.md` **only when** that file already exists pre-close, **or** use a pre-written cortex sidecar with markers.

3. `session_close` derives `handoff_prompt` server-side → `handoff_provenance.derivation=file_markers` (verified surface).

Detached-string `handoff_prompt` without anchor → advisory `handoff_missing_transcript_anchor` on the 201 response.

## Anti-patterns

| Bad | Good |
|---|---|
| Poll thread N only; no transcript ref | Transcript anchor first, then poll/delegate steps |
| Repeat decisions/files from summary inline | Point at transcript file; handoff stays short |
| `transcript_depth=none` + handoff | `light` + handoff (422 otherwise) |
<!-- /target:* -->

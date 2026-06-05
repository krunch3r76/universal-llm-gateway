<!-- target:* -->
## Handoff depth gate (HARD — server-enforced)

```
write_handoff(S)  ⟹  depth(S) ∈ {light, verbatim}
write_handoff(S)  ⟹  depth(S) ≠ none
```

`handoff_prompt` and `handoff_source_path` mirror to `transcript:{session_id}`
entity attributes. `depth=none` creates **no** transcript entity — only a journal
row fallback that boot and `entity_get(transcript:…)` do not surface.

| Operator signal | Minimum `transcript_depth` |
|---|---|
| Requested handoff / continuation for next session | **`light`** |
| Ceremonious close / full turn walk-back | `verbatim` |
| Mechanical close, no handoff | `none` OK |

**Web/API seats:** `light` uses `session_summary_md` as the on-disk file — no
`transcript_md` required. Absence of a JSONL file is not grounds for `none`
when a handoff is requested.

**422:** `handoff.requires_transcript_entity` — retry with `light` or `verbatim`.

**Transcript anchor:** `handoff_prompt` must cite `transcript:{session_id}` (or
`notes/system/transcripts/{session_id}.md`) so the next session can load closing
context — see `session-close-handoff.md`.

Kernel FOL: `agent-skills/session-close-kernel.md` § FOL pipeline · § Depth dial.
<!-- /target:* -->

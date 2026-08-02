---
name: web-transcript-preprocessing
description: "Immediately before session_close on web seats, preprocess transcript_md: retain descriptive metadata and narrative, strip payload content, and never reproduce uploaded file contents."
trigger_match_terms: ["web-transcript-preprocessing", "web_transcript_preprocessing", "pre-close", "transcript_md", "preprocess", "web", "session_close", "dry_run", "uploaded file contents"]
---

# Web Transcript Preprocessing — Session Close

`web_seat ∧ before(session_close)` ⇒ preprocess `transcript_md` in context before the write. Pass the preprocessed markdown inline as `transcript_md` with `session_summary_md` and `summary`. Do not invent a source flag or JSONL path for web sessions.

Recommended preflight: `session_close(dry_run=True, transcript_md=…, handoff_prompt=…, …)` before real close. Fix any returned `would_fail/reason` before the writing call.

## Core rule

For every tool call/response block:

`field_describes_touched_record ⇒ keep`  
`field_is_payload_content ⇒ strip`

Test: Would a future reader know what happened without this field?

## Keep

- Tool names and call parameters.
- File paths, URIs, IDs, names, descriptions, sections, directory listings.
- Status/outcome signals, success/failure codes, warnings, error messages/details.
- Bus turn subjects and bodies.
- Decision text, synthesis, reasoning prose.
- `execution_id`; strip pipeline token/duration/poll scaffolding.

## Strip

- File body content from `fs(read)`; keep path/section/char count.
- Full assertion arrays from `entity_get` / `assertions()`; keep entity ID/name/description.
- Echoed `transcript_md` from prior responses.
- Pipeline scaffolding: `prompt_tokens`, `completion_tokens`, `total_tokens`, `reasoning_tokens`, `duration_s`, poll URLs.
- Uploaded file contents. Record filename, file type, structural overview, and session use only.
- Raw retrieval payloads: large JSON arrays/objects when their narrative content is captured elsewhere.

## Prohibitions

- Do not trim or summarize prose reasoning, decisions, or synthesis.
- Do not collapse tool calls to one-liners; keep parameters visible.
- Do not strip warnings, error details, bus turn bodies, execution IDs, entity IDs, or reference identifiers.
- Never reproduce uploaded file contents in `transcript_md`.

## Responsibility boundary

Web does this before `session_close`. The pipeline does not trim because metadata-vs-payload judgment is context-sensitive. The stored transcript should already be the right shape; the verbatim conversation remains recoverable server-side by session reference.

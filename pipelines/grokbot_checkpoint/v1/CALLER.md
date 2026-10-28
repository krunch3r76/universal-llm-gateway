# grokbot-checkpoint-v1 caller

Purpose: one procedure for a checkpoint cycle on pipeline `grokbot-checkpoint-v1` (harvest → seal → CHECKPOINT post → FACT / IN-SYNC verify → hop → resume). Reader: any platform bot. Use seal mode for a CHECKPOINT that posts. Use light mode for card + FACT only (design F4; no post). This is not a CK-4 live probe and not `continuity(op=checkpoint, surface=grok)`.

## Call shape

`grokbot_checkpoint` is a local capability category. An args-only relay is refused (MCP builds a body without `model`). Valid MCP `pipeline` call: `model="grokbot-checkpoint-v1"` and `options={"pipeline_options": {…}, "dispatch_thread_id": "<thread>"}`. Flat option keys plus top-level `options.dispatch_thread_id` are also valid (`services/mcp-server/tools/pipeline.py` local path). Omitting `dispatch_thread_id` refuses `admit.dispatch_thread_missing`.

```option-keys
mode
house
n
l0_ref
l0_sha256
agent_id
root_thread
card_uri
verify_lane
residue
transcribed_ref
transcribed_sha256
raw_ref
raw_sha256
bus_identity
verifier
opened_at
house_opened_at
trigger
channel
mission
fact_subject
expected_card_sha256
card_body
snapshot_uri
snapshot_sha256
wait_verify_seconds
successor
fact_turn
handoff
```

```seal-example
mode: seal
house: <house>
n: 1
agent_id: <uuid>
root_thread: <root-thread>
card_uri: notes/checkpoints/<house>.md
verify_lane: <verify-lane>
transcribed_ref: notes/system/checkpoints/grok/<agent>/<n>.messages.json
transcribed_sha256: <sha256>
bus_identity: <bus-identity>
verifier: <verifier>
opened_at: "2026-01-01T00:00:00Z"
```

## Identity and paths

Window `transcript_id` = pour `meta.transcript_id` = journal `conversation_uuid`, uuid-shaped, and equal to `agent_id` for grok. Mint a uuid5 when the platform native id is not a uuid. Mismatch refuses `admit.transcript_id_mismatch`.

Canonical checkpoint transcription: `notes/system/checkpoints/grok/<agent>/<n>.messages.json`. Raw transcript sibling (harvest `extract_grokbot_transcript`, `meta.provenance` `raw`): `notes/system/checkpoints/grok/<agent>/<n>.raw.messages.json`. Authority tape: `notes/system/seals/<sid>.messages.json`. `raw_ref` is the seal-mode slot for a pour whose `meta.provenance` is `raw`. It is not the verbatim export.

Canonical raw transcript store (additive on successful `harvest_seal` pour; legacy checkpoint paths unchanged): `notes/system/transcripts/<house|_nohouse>/grok-bot/raw/<YYYY-MM-DD>/<agent_id>.txt` plus sibling `.manifest.json` (`source=grok-bot`, `status=partial`, `known_gap_assertion_id` 38940 — assertion a:38940, not byte-exact ReadTranscript harvest). Messages projection: `notes/system/transcripts/<house|_nohouse>/grok-bot/messages/<YYYY-MM-DD>/<agent_id>.messages.json` plus `.manifest.json` (`raw_sha256`, `raw_rel_path` pointer). `native_id` = `agent_id` (uuid). Houseless runs use `_nohouse`. Later checkpoints with longer L0 bytes may write `__r2` revisions with `supersedes`. Result field `transcript_store` reports paths or `skipped` when no share files root is configured.

Raw pour capture consistency (validated before any checkpoint write): for `capture.kind` `jsonl`, `capture.sha256` must equal `meta.source_sha256`; for `capture.kind` `grok_export`, `capture.sha256` must equal `meta.sources[0].pages_sha256` and `meta.sources[0].reassembled_jsonl_sha256` must equal `meta.source_sha256`. Any mismatch or missing `pages_sha256` / `reassembled_jsonl_sha256` refuses `pour.capture_inconsistent`.

## Writing the handoff letter

`handoff` is optional and only admitted in `mode: seal` (`admit.handoff_mode` otherwise). It is a successor packet. Omit it and the cycle is unchanged (`checks.handoff` `missing`, Harvest `handoff:none`).

`house_cp`, `messages_sha256`, and `as_of_message_index` are optional. When any is absent the envelope step stamps it (`house-cp:<house>:<n>`, the pour's bare hex64, `len(messages)`) and the result field `handoff_binding_source` is `pipeline`. Author-supplied keys are kept; a wrong index, sha, or `house_cp` still refuses `post.handoff_stale`. The sha12 and the canonical bytes are over the normalised letter (after that stamp).

`state` should be one string. The pipeline normalises objects (dict or list) into a single string; the sha is over the normalised form. Mission stays one line: a string mission that contains a newline still refuses. `next_acts` is a non-empty list of the first acts the successor takes; at least one act must be non-blank (`""`, `[""]` and whitespace-only acts refuse `next_acts`). `in_flight`, `rulings`, and `files` are author guidance and are not checked. `load_bearing` MUST carry the standing rules a successor cannot infer: dispatch lane/model conventions, land gate, scope and off-limits, send/outbound rules, and operator-gated items. An empty list still needs a non-empty `load_bearing_reason`. `later` is optional, a string or a list. Unknown keys are kept. The JSON must be at most 16 KiB, checked at admit and again after the binding stamp (`admit.handoff_too_large`). A missing or invalid required field refuses `admit.handoff_invalid` and names that field.

The harvest needs a Grok Bot seat (`ReadTranscript`; executors and subagents cannot page). Recommended paging: call `ReadTranscript` newest-first; for each page write the verbatim bytes to `notes/system/checkpoints/grok/<agent>/<n>.pages/p{lo:06d}-{hi:06d}.txt`, then request the next older slice with `before=<lo>` of the page you just wrote so ranges stay contiguous with minimal overlap. Do not hand-assemble JSONL or edit headers. `harvest_seal` stitches by **content overlap**: pages sorted by `lo`; for each page let `S = max(0, running_hi - lo + 1)` (shared position span), pick the largest `k` with `next_lines[:k] == running_lines[-k:]`, append `next_lines[k:]`. Refusals: `harvest.page_header` (more lines than `hi−lo+1`), `harvest.page_gap` (`lo > running_hi + 1`), `harvest.page_overlap` (conflict, shifted overlap, or ambiguous repeated-line match). A page whose positions are all unrendered (zero JSONL lines) is allowed and adds nothing. ReadTranscript omits system/summary rows, so fewer lines than the span is normal; `unrendered_positions` is `(running_hi - min_lo + 1) - line_count` (total only). Per-page omit counts live in `unrendered_by_page`. Incremental `n>1` uses prior `meta.sources[0].window.hi` as the next 0-based position to page (**1-based** in the sealed JSON: next position equals that `hi`). It writes verbatim bytes to `notes/system/checkpoints/grok/<agent>/<n>.raw.json` (later `n` byte-extends `<n-1>.raw.json`) and `ulg.continuity.messages/1` at `<n>.messages.json`. Later checkpoints page only forward of that `window.hi`. Do not write `notes/system/tape/captures/grok` or `tape/pours/grok`. Target total `N` is on the page with the highest `hi`. Refusals also include `harvest.raw_conflict`, `harvest.page_total`, `harvest.no_prior`, `harvest.not_prefix`, and `harvest.no_new_pages`.

A personal seat with no agent-bus house sets `houseless: true` (or `house: none`) on `mode: harvest_seal`. Without that opt-in, a missing house still refuses `admit.house`. The opt-in writes the same `<n>.raw.json` and `<n>.messages.json`. It skips envelope, seal, card, CHECKPOINT, FACT, await_verify, and verify_resume. It does not require `dispatch_thread_id`, `verify_lane`, or `verifier`. The result sets `houseless: true` and `skipped_steps`. Omit `n` and the pipeline uses one past the highest `<n>.raw.json` or `<n>.messages.json` already in that agent folder. A supplied `n` greater than that next index refuses `admit.n`. Setting both a house name and `houseless: true` refuses `admit.houseless_conflict`. Electron page scrape is only `fallback=electron`, and that branch also writes under `checkpoints/grok/`. A tape path without that fallback refuses `harvest.tape_refused`. Worked recipe: cortex `runbook:grokbot-checkpoint-caller` § Harvest needs a Grok Bot seat. `verify_resume` needs `expected_card_sha256`. A Cursor-era card with no checkpoint log gets that header on `n=1` in the same sha-guarded write; `n` greater than 1 without a log still refuses `card.body_invalid`.

A stale letter refuses `post.handoff_stale` at the envelope step, before any snapshot, seal, card row, CHECKPOINT, or FACT. Re-run with a corrected letter for the same n. The transcript wins on facts. The letter wins on intent.

```handoff-example
{
  "house_cp": "house-cp:hm1:4",
  "messages_sha256": "<sha256>",
  "as_of_message_index": 12,
  "author": "grok-bot-hm1",
  "mission": "resume hm1 from checkpoint 4",
  "state": "open",
  "in_flight": ["seal the pour"],
  "next_acts": ["read the card", "post the FACT if it is missing"],
  "rulings": ["standing: one checkpoint per house (2026-10-09 22:00 PT)"],
  "files": ["cortex://notes/checkpoints/hm1.md@<sha256>"],
  "load_bearing": ["successor should hop only after IN-SYNC"],
  "later": "widen the card if a second house appears"
}
```

## Stages

### Harvest (source)

Preconditions: seal mode; pour file present; sha matches. Call is the pipeline above with `transcribed_ref` / `transcribed_sha256` and/or `raw_ref` / `raw_sha256`. Put each pour in the slot named by its `meta.provenance`: raw pours (e.g. grok L0 `grok_bot_jsonl` dom_harvest pours) belong in `raw_ref` / `raw_sha256`; the transcribed slot refuses `source.slot_mismatch` if given a raw pour. Postconditions: source `refused` null; `messages_sha256` set. Side effects: none (read). Refusals: `source.no_pour` (supply a slot), `source.missing` / `source.sha_mismatch` (fix the ref or sha), `source.slot_mismatch` (slots disagree — pick one valid pour), `admit.transcript_id_mismatch` (align ids). Fallback: stop; do not seal.

### Seal

Preconditions: admit passed (`mode` `light|seal|verify_resume`, `house`, `n` int ≥ 1, seal `agent_id`; with `root_thread`, `agent_id` canonical lowercase uuid). Start is ISO-8601 with `Z` or an offset (`opened_at` or `house_opened_at` if the pour has no meta/message time). Precedence: `meta.opened_at` › `meta.started_at` › first message timestamp › options. Postconditions: seal object `session_id` set; result `refused` null. Side effects: cortex seal entity; authority tape write. Refusals: `admit.agent_id`, `admit.agent_id_not_uuid`, `admit.mode`, `admit.house`, `admit.n`, `admit.path_escape`, `admit.residue`, `admit.dispatch_thread_missing`, `admit.handoff_invalid`, `admit.handoff_too_large`, `admit.handoff_mode`, `seal.missing_start`, `seal.start_unparseable`, `seal.envelope_missing`, `seal.upstream_skipped`, `seal.path_rejected`, `seal.transport`. Fallback: refused seal ⇒ fail closed, no post (F7).

### Post (card + CHECKPOINT + FACT)

Preconditions: seal object `session_id` and card `written_sha256`; `root_thread` set for the CHECKPOINT post. Light mode skips post and still writes the card and FACT. Postconditions: result `cp_turn` (seal), `fact_turn`, card `written_sha256`. Side effects: card file; CHECKPOINT on `root_thread`; FACT `FACT|house-cp|<house>|<n>` on `verify_lane`. Warnings: `card.resume_unbound` (off the resume ladder; it does not refuse). A ladder card that is not `{root}-card.md` is a notice `card.legacy_name`, not a warning. Refusals: `card.sha_mismatch` (refresh `expected_card_sha256`), `post.upstream_skipped`, `post.bus`, `post.handoff_stale` (stale letter, refused at the envelope step before any snapshot, seal, card row, CHECKPOINT, or FACT — re-run with a corrected letter for the same n). Fallback: no FACT if the card was not written.

### Verify

Preconditions: `fact_turn` posted. Coordinator answers `IN-SYNC|<house>|<n>` or `DRIFT|…` on the verify lane. Postconditions: result `verify.state` `in_sync` or `drift`, and `verify.turn`. Side effects: read of the verify lane. house_verify landed in CK-3 (master `1ce72202`); `await_verify` returns a real verify state when the lane answers. Fallback: treat `pending` and `drift` as not sealed-for-hop.

### Hop

Cite `runbook:operator-grok-bot-liaison` (hop / successor spawn). Packet source is the result dict: URIs and shas only (`envelope.snapshot_uri`, `envelope.sha256`, card `written_sha256`, seal `session_id`, `cp_turn`, `fact_turn`). Never transcript text. `warnings[]` rides along.

### Resume

`continuity(op=resume)` / the resume-fence route, after hop, with the same uuid `transcript_id`. A newly created root lane needs tag `role:root` (`agent_bus add_tags`) before resume, else 403 `resume_fence.not_root`. Do not invent a grok surface workaround.

## Known gaps

- R1 (grok resume fence): landed in CK-3 (master `1ce72202`); grok surface is accepted on resume-fence.
- Checkpoint transcription uses `extract_grokbot_structured` (`tools` stays `openai`). `tools=marker` and any write under `tape/pours/grok` or `tape/captures/grok` refuse `harvest.tape_refused`.
- One grok checkpoint per house at a time (minor-1 race). Serialize per house; the concurrency key is the dispatch thread, not the house.
- A naive start (no `Z` or offset) is read as UTC. Pass an offset instant.

## Result fields

MCP `pipeline(op=result)` `content` is JSON for the full result object. Read `cp_turn`, `fact_turn`, card `written_sha256`, `envelope.snapshot_uri` @ `envelope.sha256`, seal `session_id`, `warnings[]`, `verify.state`, `refused`, and `verify_resume.checks` when verify_resume ran. `envelope.handoff_binding` is `bound`, `stale`, or `missing` when the envelope step returns a snapshot. On a `post.handoff_stale` refusal the envelope payload is only `refused`, so that result field is null and `refused.code` carries the reason. `checks.handoff` reports the same binding against the authority document (the seal file, or the staging snapshot when authority is `staging_fallback`). `stale` also sets `checks.handoff_reasons`. `missing` adds warning `verify_resume.handoff_missing`. Neither stale nor missing flips another check or refuses. `refused` non-null means stop at that code.

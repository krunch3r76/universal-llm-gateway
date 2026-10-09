# grokbot-checkpoint-v1 caller

Purpose: one procedure for a checkpoint cycle on pipeline `grokbot-checkpoint-v1` (harvest → seal → CHECKPOINT post → FACT / IN-SYNC verify → hop → resume). Reader: any platform bot. Use seal mode for a CHECKPOINT that posts. Use light mode for card + FACT only (design F4; no post). This is not a CK-4 live probe and not `continuity(op=checkpoint, surface=grok)`.

## Call shape

`grokbot_checkpoint` is a local capability category. An args-only relay is refused (MCP builds a body without `model`). Valid MCP `pipeline` call: `model="grokbot-checkpoint-v1"` and `options={"pipeline_options": {…}, "dispatch_thread_id": "<thread>"}`. Flat option keys plus top-level `options.dispatch_thread_id` are also valid (`services/mcp-server/tools/pipeline.py` local path). Omitting `dispatch_thread_id` refuses `admit.dispatch_thread_missing`.

```option-keys
mode
house
n
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
```

```seal-example
mode: seal
house: <house>
n: 1
agent_id: <uuid>
root_thread: <root-thread>
card_uri: notes/checkpoints/<house>.md
verify_lane: <verify-lane>
transcribed_ref: tape/pours/<surface>/<transcript_id>.messages.json
transcribed_sha256: <sha256>
bus_identity: <bus-identity>
verifier: <verifier>
opened_at: "2026-01-01T00:00:00Z"
```

## Identity and paths

Window `transcript_id` = pour `meta.transcript_id` = journal `conversation_uuid`, uuid-shaped, and equal to `agent_id` for grok. Mint a uuid5 when the platform native id is not a uuid. Mismatch refuses `admit.transcript_id_mismatch`.

Canonical pour: `tape/pours/<surface>/<transcript_id>.messages.json`. Authority tape: `notes/system/seals/<sid>.messages.json`.

## Stages

### Harvest (source)

Preconditions: seal mode; pour file present; sha matches. Call is the pipeline above with `transcribed_ref` / `transcribed_sha256` and/or `raw_ref` / `raw_sha256`. Postconditions: source `refused` null; `messages_sha256` set. Side effects: none (read). Refusals: `source.no_pour` (supply a slot), `source.missing` / `source.sha_mismatch` (fix the ref or sha), `source.slot_mismatch` (slots disagree — pick one valid pour), `admit.transcript_id_mismatch` (align ids). Fallback: stop; do not seal.

### Seal

Preconditions: admit passed (`mode` `light|seal|verify_resume`, `house`, `n` int ≥ 1, seal `agent_id`; with `root_thread`, `agent_id` canonical lowercase uuid). Start is ISO-8601 with `Z` or an offset (`opened_at` or `house_opened_at` if the pour has no meta/message time). Precedence: `meta.opened_at` › `meta.started_at` › first message timestamp › options. Postconditions: seal object `session_id` set; result `refused` null. Side effects: cortex seal entity; authority tape write. Refusals: `admit.agent_id`, `admit.agent_id_not_uuid`, `admit.mode`, `admit.house`, `admit.n`, `admit.path_escape`, `admit.residue`, `admit.dispatch_thread_missing`, `seal.missing_start`, `seal.start_unparseable`, `seal.envelope_missing`, `seal.upstream_skipped`, `seal.path_rejected`, `seal.transport`. Fallback: refused seal ⇒ fail closed, no post (F7).

### Post (card + CHECKPOINT + FACT)

Preconditions: seal object `session_id` and card `written_sha256`; `root_thread` set for the CHECKPOINT post. Light mode skips post and still writes the card and FACT. Postconditions: result `cp_turn` (seal), `fact_turn`, card `written_sha256`. Side effects: card file; CHECKPOINT on `root_thread`; FACT `FACT|house-cp|<house>|<n>` on `verify_lane`. Warnings: `card.resume_unbound` / `card.nonconforming` (fix the card binding; they do not refuse). Refusals: `card.sha_mismatch` (refresh `expected_card_sha256`), `post.upstream_skipped`, `post.bus`. Fallback: no FACT if the card was not written.

### Verify

Preconditions: `fact_turn` posted. Coordinator answers `IN-SYNC|<house>|<n>` or `DRIFT|…` on the verify lane. Postconditions: result `verify.state` `in_sync` or `drift`, and `verify.turn`. Side effects: read of the verify lane. Until house_verify (CK-3) lands, `await_verify` may return `verify.state=pending`. Fallback: treat `pending` and `drift` as not sealed-for-hop.

### Hop

Cite `runbook:operator-grok-bot-liaison` (hop / successor spawn). Packet source is the result dict: URIs and shas only (`envelope.snapshot_uri`, `envelope.sha256`, card `written_sha256`, seal `session_id`, `cp_turn`, `fact_turn`). Never transcript text. `warnings[]` rides along.

### Resume

`continuity(op=resume)` / the resume-fence route, after hop, with the same uuid `transcript_id`. Do not invent a grok surface workaround.

## Known gaps

- R1: `libs/agent_bus_store/routes/threads/resume_fence.py` accepts only `surface` `cursor` or `claude_ai`, so a grok resumer gets 422 until CK-3. State the dependency; do not work around it.
- Verify `pending` until house_verify lands.
- One grok checkpoint per house at a time (minor-1 race). Serialize per house; the concurrency key is the dispatch thread, not the house.
- A naive start (no `Z` or offset) is read as UTC. Pass an offset instant.

## Result fields

Read `cp_turn`, `fact_turn`, card `written_sha256`, `envelope.snapshot_uri` @ `envelope.sha256`, seal `session_id`, `warnings[]`, `verify.state`. `refused` non-null means stop at that code.

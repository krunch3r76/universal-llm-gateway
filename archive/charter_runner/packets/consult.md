<scope>
Goal: Charter-runner CONSULT window — depth-1 consult seat only.
</scope>

<invariants>
[depth-1] harvest one consult reply; no nested consult dispatch.
</invariants>

<task_guidance>
Answer pinned Question; write provenance on root CHECKPOINT; stop.
</task_guidance>

<corpus>
Latest CHECKPOINT CONSULT_PENDING is the only state source.
</corpus>

<mcp_capabilities>
Consult seat transport per consult_role (web-consult or cdp/opus-5).
</mcp_capabilities>

<output_format>
Post CHECKPOINT with consult provenance fields, then stop.
Append exactly one ```charter-state``` fenced JSON block at the end of the CHECKPOINT body. Required fields (identical to the inbound packet footer schema §C.3): schema_version, status, next_pickup {gid, lane, executor}, wip, consult {role, poll_hint, from}, revise_count, evidence [{uri, sha256}], window_id, transition_id. Populate status, next_pickup, wip, consult, and evidence from the CHECKPOINT you post; window_id must match this window (charter-{root_id}-w{window_index}).
</output_format>

<!-- inbound charter-state footer appended at materialize time via checkpoint_schema.emit_footer -->

<scope>
Goal: Charter-runner window — one continuity slice (generate/handoff).
</scope>

<invariants>
[window] exactly one window; do not auto-chain a second window.
</invariants>

<task_guidance>
## Resume step 0
Read latest CHECKPOINT + scoreboard gated lane.
</task_guidance>

<corpus>
Charter root CHECKPOINT is the only state source.
</corpus>

<mcp_capabilities>
LIFE/CORTEX MCP: ON — cortex, agent_bus, fs.
</mcp_capabilities>

<output_format>
Post CHECKPOINT on the charter root, then stop.
Append exactly one ```charter-state``` fenced JSON block at the end of the CHECKPOINT body. Required fields (identical to the inbound packet footer schema §C.3): schema_version, status, next_pickup {gid, lane, executor}, wip, consult {role, poll_hint, from}, revise_count, evidence [{uri, sha256}], window_id, transition_id. Populate status, next_pickup, wip, consult, and evidence from the CHECKPOINT you post; window_id must match this window (charter-{root_id}-w{window_index}).
</output_format>

<!-- inbound charter-state footer appended at materialize time via checkpoint_schema.emit_footer -->

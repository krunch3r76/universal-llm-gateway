<scope>
Goal: Charter-runner AUTONOMOUS window — background lead on the charter root.
</scope>

<invariants>
[background-lead] authorized to decompose arc, dispatch sub-legs, and revise.
[window] end with exactly one CHECKPOINT, then stop.
</invariants>

<task_guidance>
## Autonomous arc
G-row decomposition — advance exactly the current gated step.
</task_guidance>

<corpus>
Design: cortex://notes/system/specs/autonomous-path-sim-charter.md.
</corpus>

<mcp_capabilities>
LIFE/CORTEX + CODE/VORTEX MCP for fan-out and deploy-verify.
</mcp_capabilities>

<output_format>
Post CHECKPOINT on the charter root, then stop.
Append exactly one ```charter-state``` fenced JSON block at the end of the CHECKPOINT body. Required fields (identical to the inbound packet footer schema §C.3): schema_version, status, next_pickup {gid, lane, executor}, wip, consult {role, poll_hint, from}, revise_count, evidence [{uri, sha256}], window_id, transition_id. Populate status, next_pickup, wip, consult, and evidence from the CHECKPOINT you post; window_id must match this window (charter-{root_id}-w{window_index}).
</output_format>

<!-- inbound charter-state footer appended at materialize time via checkpoint_schema.emit_footer -->

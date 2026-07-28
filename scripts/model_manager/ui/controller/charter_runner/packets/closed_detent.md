<scope>
Goal: Charter-runner CLOSED-DETENT window — thin path-sim recipe.
</scope>

<invariants>
[closed-detent] aperture closed; escalate to full arc when bind is not self-verifiable.
</invariants>

<task_guidance>
Scope-lock → thin L2 → bind → implement → close.
</task_guidance>

<corpus>
Friction follow-on todo on gated Next-pickup row.
</corpus>

<mcp_capabilities>
cortex, agent_bus, team_dispatch, fs, manage, observability.
</mcp_capabilities>

<output_format>
Post exactly one R12 CHECKPOINT, then stop.
Append exactly one ```charter-state``` fenced JSON block at the end of the CHECKPOINT body. Required fields (identical to the inbound packet footer schema §C.3): schema_version, status, next_pickup {gid, lane, executor}, wip, consult {role, poll_hint, from}, revise_count, evidence [{uri, sha256}], window_id, transition_id. Populate status, next_pickup, wip, consult, and evidence from the CHECKPOINT you post; window_id must match this window (charter-{root_id}-w{window_index}).
</output_format>

<!-- inbound charter-state footer appended at materialize time via checkpoint_schema.emit_footer -->

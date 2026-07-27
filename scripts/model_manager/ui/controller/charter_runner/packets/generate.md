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
</output_format>

<!-- charter-state footer appended at materialize time via checkpoint_schema.emit_footer -->

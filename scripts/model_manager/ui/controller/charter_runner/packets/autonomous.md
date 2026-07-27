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
</output_format>

<!-- charter-state footer appended at materialize time via checkpoint_schema.emit_footer -->

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
</output_format>

<!-- charter-state footer appended at materialize time via checkpoint_schema.emit_footer -->

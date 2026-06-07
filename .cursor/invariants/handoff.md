# Handoff Invariants

Use this block when building agent-to-agent handoff packets. The canonical
protocol is `/mnt/torus/projects/.cursor/rules/architecture-handoff-protocol.mdc`.

- Every handoff packet contains `<scope>`, `<invariants>`, `<task_guidance>`,
  `<corpus>`, optional `<mcp_capabilities>`, and `<output_format>`.
- Compose invariants from universal parent rules, workspace `_ws.mdc` overlays,
  and task-specific constraints.
- Keep the invariant block compact: one actionable constraint per line.
- MCP-equipped reviewers must cite tool evidence inline.
- Non-MCP reviewers receive enough corpus to review without live reads.
- Every finding is locally validated before application.
- Reject findings that contradict workspace rules.
- Surface out-of-scope findings for triage instead of silently applying them.
- Iterate until a convergence signal, explicit user cap, or stable disagreement.
- Write a review artifact with findings, applied fixes, rejections, surfaced
  triage items, and iteration history.

# Workspace Invariants

These files are compact, prompt-facing invariant blocks for tools and frontier
handoffs. Cursor rules remain canonical; this directory is a stable repo-relative
cache for commands that need a plain markdown input.

## Files

- `modularize.md` — injected by `scripts/modularize plan` before source code.
- `review.md` — general code/doc review invariant block for handoff packets.
- `handoff.md` — packet-construction rules for agent-to-agent review.

## Maintenance

Update these files when shared parent rules or workspace `_ws.mdc` overlays
change in a way that affects tool prompts. Keep them short and imperative.

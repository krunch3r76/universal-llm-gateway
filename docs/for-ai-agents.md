# For AI agents

If you are an agent encountering this repo for the first time:

1. **Boot:** `cortex_brief(seat="your_seat")` returns session state, life context, continuation, and operational context.
2. **Tool reference:** [docs/tool-reference.md](tool-reference.md) lists MCP tool signatures, sandbox routing, and the dispatch catalog.
3. **Agent guide:** [AGENTS.md](../AGENTS.md) covers identity, the cortex boot ritual, MCP wiring, and the session-close protocol. Cursor IDE users also get `.cursor/commands/cortex-brief.md`, which is IDE-local and not part of this repo.
4. **Cortex skills:** `agent_skill` entities surfaced by boot; read full skill docs from `agent-skills/<NAME>.md` in the Cortex sandbox.
5. **Feature registry:** [docs/cursor/cortex-registry.md](cursor/cortex-registry.md) covers belief revision primitives and operational extensions, plus the classification guide.
6. **Boot sequence:** `notes/system/shared/boot-sequence.md` (cortex sandbox) covers boot flow and on-demand modules.

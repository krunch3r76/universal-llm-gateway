<!-- frontmatter:cursor
description: Cursor session boot and close — mode selection, transcript entity procedure
alwaysApply: false
-->
<!-- target:cursor -->
# Cursor Boot (Workspace)

## Mode Selection

Every IDE-resident Cursor agent operates in one of three modes per session. Select the
`agent` parameter for `cortex_boot` based on the active model family:

| Active model family | `cortex_boot(agent=...)` value |
|---|---|
| Anthropic Claude (Sonnet / Opus) | `"claude-cursor"` |
| OpenAI GPT family | `"gpt-cursor"` |
| xAI Grok family | `"grok-cursor"` |
| Google Gemini family | `"gemini-cursor"` |

| Mode | Trigger | Boot |
|---|---|---|
| **Code** (default) | Engineering tasks, debugging, code review | Minimal — MCP tool reference only |
| **Continue** | Opening message contains `transcript:cursor-YYYY-MM-DD-HHmm` | One targeted transcript read — Session Summary only |
| **Universal** | User says "universal mode" or opens with non-engineering topic | Full — `cortex_boot(agent=<family>)` per the table above + `notes/system/shared/boot-sequence.md` |

## Continue Mode — Lightweight Transcript Resume (MANDATORY)

When the opening message contains a `transcript:cursor-YYYY-MM-DD-HHmm` ID:

Execute the Continue Mode protocol from `mcp-tool-awareness_ws.mdc` — three
parallel `CallMcpTool` reads (session summary, activity journal, recent transcripts).

**Do NOT**: call `cortex_boot`, load full transcripts, or read the boot narrative.
**Cost**: ~5-10KB total. Fast enough to not waste context before work starts.

This mode activates on the transcript ID alone — no `@cortex-essentials.mdc`
attachment required. If `@cortex-essentials.mdc` is also attached, still use
Continue Mode (lightweight) unless the user explicitly says "universal mode".

## Code Mode — Minimal Boot (MANDATORY)

MCP tool awareness is always available via `mcp-tool-awareness_ws.mdc`.

On the **first MCP tool call** beyond basic `fs` ops, if you need deeper tool
knowledge, read the canonical reference:

```
CallMcpTool(server="user-vortex", toolName="fs", arguments={
  "op": "md_read", "sandbox": "workspaces",
  "path": "universal-llm-gateway/docs/tool-reference.md", "section": "fs"
})
```

**Skip if**: the session already has tool context from a prior turn, or from
`cortex_boot`, or from reading `docs/tool-reference.md` directly.

## Universal Mode — Slim Boot

When Universal Mode activates:
1. `cortex_boot(agent="claude-cursor")` (or appropriate seat slug) —
   returns `briefing_card` (~3-5KB), `sections_available` manifest, and
   `operational_context_ref`
2. Render `briefing_card` and surface open_items as agenda
3. Pull deeper sections on demand via manifest hints (todos, sessions, bus, etc.)
4. Operational context available at `operational_context_ref` — read sections via `fs md_read` when needed
5. Apply boot-time tier check — assess first queued task from the agenda against
   escalation triggers. Load `model-tier-awareness.mdc` on demand (already available
   via `model-tier-stub.mdc` always-applied rule). Emit advisory tier note if any
   trigger fires; if the opening message contained a user-supplied model identity,
   apply the blocking protocol instead.

## Session Close — MANDATORY on "close session" / "session end" / "/session-end"

**Authoritative procedure lives in `core_ws.mdc` (always applied).**

For reliable closes (post 2026-05-16 server-side refactor), resolve the
current session's Cursor agent-transcripts JSONL path and compose the
kilobyte-scale structural layer (`session_summary_md`), then call the
atomic `cortex(tool="session_close", arguments='{"session_id": "...",
"agent": "claude-cursor", "transcript_jsonl_path": "<path>",
"session_summary_md": "## Session Summary\n\n...", "summary": "..."}')`.
cortex-api reads the JSONL, assembles the verbatim layer
server-side, writes the file, and performs the validated atomic DB
commit (entity + journal + edge). The 201 response carries
`content_hash` (`sha256:<hex>`), `turn_count`, and `byte_count` in
addition to `transcript_entity_id` / `transcript_path` /
`journal_row_id`. The old `dispatch(tool="session_close")` reminder
path is deprecated and does not perform the close (see agent-bus thread
824 / 2026-05-01-2059 hallucination). Verify 201 response (all four
key fields incl. `content_hash`) before reporting success.
Authoritative protocol lives in `session-close.mdc`.

**`seeded_by` on assertions is family-level** (server-normalized seat→family); do not pass a seat slug in `seeded_by`.

## Reference Files (read on demand)

- `universal-llm-gateway/docs/tool-reference.md` — canonical MCP tool signatures and full op catalog
- Sandbox routing and markdown ops: see `mcp-tool-awareness_ws.mdc` (always loaded)
<!-- /target:cursor -->

<!-- target:* -->
## Schema-Change Discipline

∀ schema modification (`CREATE INDEX`, `CREATE TABLE`, `ALTER TABLE`, `DROP INDEX`,
`CREATE TRIGGER`, etc.) against any sqlite database in the ecosystem (cortex, RAG,
events, agent-bus): land via `libs/<store>/migrations/NNN_*.sql` or `.py`.

- ❌ Ad-hoc Python or `sqlite3` CLI against the live DB
- ❌ "Verification" or "smoke-test" scripts that mutate live state
- ✅ Migration file in `libs/<store>/migrations/` with `IF NOT EXISTS` for idempotency
- ✅ Verification against `:memory:` or a tmp-copy DB
- ✅ Apply via the migration runner (`manage` sync_restart of the owning service)

**File-creation override.** The base system prompt's *"Do not create files unless
absolutely necessary, prefer editing existing"* rule **does not apply** to migration
files. A new schema state requires a new migration file by convention — this is a
recognized exception, not a violation of the guideline.

**Anti-pattern.** `python -c '...CREATE INDEX...'` against `~/.cortex/cortex.db` to
"verify" or "smoke test", even with a backup taken first. That is a production
mutation, not a verification. EXPLAIN / PRAGMA verification runs in code against
`:memory:` or a tmp copy.

Authoritative gate: `agent_skill:lead-seat-boot` Gate 4.
<!-- /target:* -->

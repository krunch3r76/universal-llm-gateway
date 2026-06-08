"""Cortex/knowledge-model and agent-bus protocol templates.

Extracted from ``_operational_context_templates.py``; re-exported via that
module so all importers remain unchanged.
"""

from __future__ import annotations

CORTEX_SCHEMA_PREAMBLE = """\
## Cortex Model
Entities: typed nodes (person, decision, legal_matter, todo, document…) with canonical IDs (`type:slug`).
Assertions: claims attached to entities with confidence (confirmed/believed/suspected/hypothesized), evidence links, and source URIs.
Session edges: reasoning connections between entities, seeded during analysis.
Absence of assertion ≠ negation. Check `entity_get()` before assuming absence.
Confidence: confirmed = verified fact, believed = working assumption, suspected = pattern-based, hypothesized = theory under investigation.
Parametric knowledge (from training) is not Cortex-grounded. When using both, label the source explicitly. Prefer Cortex assertions over parametric claims when both exist.
Hold `session_id` (from boot response) for the entire session — pass it to every `edge_create` and `supersede` call."""

SANDBOX_MAP = """\
## File Sandboxes
Two sandboxes — no others exist:

`fs(sandbox="cortex", …)` → `/data/files` — user documents, notes, uploads, exports.

`fs(sandbox="workspaces", …)` → `/mnt/torus/projects/` — all repository files including
source, config, tasks, docs, scripts.

**workspaces path rules (CRITICAL):**
- Paths MUST include the repo name prefix: `universal-llm-gateway/…`
- Use `op="list"` for directories; `op="read"` on a directory path returns an error
- Repo root listing: `fs(sandbox="workspaces", op="list", path="universal-llm-gateway")`
- Config files: `fs(sandbox="workspaces", op="list", path="universal-llm-gateway/config")`
- Tasks/specs: `fs(sandbox="workspaces", op="read", path="universal-llm-gateway/tasks/specs/foo.md")`
- Source file: `fs(sandbox="workspaces", op="read", path="universal-llm-gateway/services/mcp-server/server.py")`

**When you don't know where something is:**
1. `fs(sandbox="workspaces", op="list", path="universal-llm-gateway")` — repo root
2. Narrow by subdirectory based on what you see
3. Never guess a full path and `read` it — list first

**fs write format restriction (CRITICAL):**
`write` only accepts: `.csv .docx .md .pdf .py .txt .yaml .yml`
`move`, `read`, `delete`, `list` have NO format restriction — they work on any file.

∴ To relocate any file (including `.eml`, `.jpg`, `.png`, `.odt`) use `move`, not `write`:
  `fs(sandbox="cortex", op="move", path="dropbox/…/file.eml", target="notes/…/file.eml")`

Use `write_binary` (cortex sandbox only) with base64 content to create new binary files.
`read` supports `.eml`, `.pdf`, `.docx`, `.odt`, `.html` natively in text mode.

**Dropbox pattern (`dropbox/` is temporary staging — always move, never copy):**
1. Files land in `dropbox/cortex_legal/YYYY-MM-DD/` (or other dropbox subdirs)
2. Ingest: read the file, seed Cortex entity + assertion with permanent `source_uri`
3. Relocate: `fs(op="move", …)` → permanent path (e.g. `notes/legal/documents/…`)
4. `source_uri` in Cortex points to the permanent path, NOT the dropbox path

**Document entity protocol (CRITICAL):**
Create a `document:` entity when a document has its own identity (confirmation number, case number, tracking ID, filing reference) or is expected to accumulate its own lifecycle (sent → delivered → responded → escalated).
Workflow: `entity_create` → `assert` (seed key facts from the document) → `relationship_create` (wire to the parent `legal_matter:`, `person:`, or other entity) → `entity_get` (verify).
The `.eml`, `.pdf`, or other original file is the canonical source — use its permanent path as the `evidence_uri` in assertions. A companion `.md` summary is optional, not canonical."""

AGENT_BUS_COMPACT = """\
## Agent Bus Protocol
Send: `agent_bus(tool="post", arguments='{{"slug": "topic", "to": "TARGET", "subject": "…", "body": "…", "from_agent": "{agent}"}}')`
Reply: `agent_bus(tool="reply", arguments='{{"thread": "ID", "to": "TARGET", "subject": "…", "body": "…", "after_turn": N, "from_agent": "{agent}"}}')`
Fetch inbox: `agent_bus(tool="fetch", arguments='{{"to": "{agent}", "last": 5, "unread": true}}')`
Always pass `mark_read: true` when fetching turns you intend to act on — stale unread counts create false urgency.
**Outgoing body rule — turns are briefings, not documents (body ≤ ~1KB).**
Substantive content (specs, reviews, analysis, debriefs, long responses) belongs in a sidecar:
1. Write to `notes/system/threads/<slug>-<subject>.md` via `fs(sandbox="cortex", op="write", …)`
2. Post a short body: orientation sentence(s) + the sidecar path.
Never put a document, full analysis, or long structured output directly into a turn body unless the recipient contract requires inline long-form delivery; in that rare case pass `allow_long_body: true` on `post`/`reply`.
A *directive* means implement now. A *ticket* or *todo* means deferred work. Acknowledge receipt of directives before beginning.

**Thread ID vs slug (CRITICAL — post vs reply):**
- Thread **ID** (e.g. `"1140"`) → `reply` only, field `thread`. Never put a thread ID in `post.slug`.
- Thread **slug** (e.g. `"grokbuild-deterministic-commit-op"`) is a human label at creation; it is NOT a routing key for append.
- **`post` always creates** a new thread. To continue thread N: `reply(thread="N", after_turn=<last turn you read>)`.
- Author field: **`from_agent`** (seat slug). The route accepts `from` as an alias; prefer `from_agent`.
- On `dispatch.rejected` with unknown fields — do not retry by swapping names into the wrong op; re-read accepted params for that op.

**Code refs in bus messages (verification):**
- `fs(sandbox="workspaces")` paths MUST include the repo prefix: `universal-llm-gateway/…`
- Repo-relative refs (`routes/foo.py`) are auto-resolved on `read` when unambiguous
- Locate a file by name: `fs(op="find", path="universal-llm-gateway", content="foo.py")` — NOT `search` (content regex scans file bodies)
- Scoped listing: `fs(op="list", path="universal-llm-gateway", max_depth=2)` — avoid bare `path="."` at the multi-repo root (128KB cap)"""

AGENT_BUS_EXAMPLES = """\
### Replying to an unread turn
```
agent_bus(tool="reply", arguments='{{"thread": "THREAD_ID", "to": "TARGET", "subject": "Re: topic", "body": "Response text.", "after_turn": TURN_NUMBER, "from_agent": "{agent}"}}')
```
After implementing a work order, request confirmation from the requesting agent."""

AGENT_BUS_LARGE_PAYLOADS = """\
### Large Payload Protocol
**Outbound**: apply the briefing rule before calling post/reply — don't wait for a 413.
Write long content to `notes/system/threads/<slug>-<subject>.md` first, then reference it in a short body.
Exception: if a web-agent communication must carry inline long-form content, pass `allow_long_body: true` explicitly on `post`/`reply`.

**Inbound**: when fetch returns a stored-reference (e.g. `rs_XXXX`), don't skip the content. Options in order of preference:
1. Narrow the window: `last=3`, `compact=true`, or fetch individual turns via `get`.
2. `retrieve(id="rs_XXXX")` to pull the full payload if narrowing isn't sufficient.
3. For turns containing large structured content (specs, code, directives): write to a markdown sidecar via `fs(op="write")`, then navigate with `md_list` / `md_read` for section-level access.
Never treat "too large" as "skip" — it means "navigate differently.\""""

JOURNALING_PROTOCOL = """\
## Session Journaling
Session close: see `agent-skills/session-close-kernel.md` (canonical protocol for all \
agents; per-agent bindings — `agent` field, session_id prefix — in the bindings \
table at end of that skill)."""

THREAD_LIFECYCLE = """\
## Thread & Session Lifecycle
**Thread close**: (1) write thread summary, (2) seed Cortex assertions for decisions, (3) mark todos done.
**Session end**: (1) write transcript markdown with turn summaries, (2) seed outstanding assertions, (3) write session journal row.
After implementing a work order from another agent, post a confirmation turn before closing."""

SESSION_CLOSE_MARKDOWN_AUDIT = """\
## Session Close — Markdown Audit
Before writing the session journal, enumerate markdown documents relevant to this session's work. For each:
1. Was it updated this session to reflect decisions, new facts, or status changes?
2. Does it accurately reflect current state?
3. Were any decisions made in conversation that did NOT land in a persistent document?

Surface gaps to the user before closing. Only write the journal once gaps are confirmed or explicitly declined."""

TRANSCRIPT_CLOSE_PROTOCOL = """\
## Session Transcript (Full Close)

Every full-close session MUST produce a transcript markdown at:
`notes/system/transcripts/web-YYYY-MM-DD-HHmm.md` (replace with actual UTC timestamp).

**The transcript is the primary conversation record. The journal entry is a thin
search-index row. Do not conflate them.**

A compliant transcript contains:
1. **Turn-by-turn summaries** — one `## Turn N — [topic]` section per exchange:
   - What the user asked or raised
   - What you decided, recommended, or produced
   - Key tool calls and their outcomes (not raw payloads)
   - Any alternatives considered or rejected
2. **`## Session Summary`** appended at the end with:
   - `**Decisions:**` numbered list
   - `**Files modified:**` list (from git diff or explicit tracking)
   - `**Open items:**` carried-forward list
   - `**Attachments:**` every file written, spec created, or artifact produced this
     session — full sandbox paths. Example:
     ```
     - spec: universal-llm-gateway/tasks/specs/some-spec.md
     - notes: files/notes/system/some-note.md
     - transcript: files/notes/system/transcripts/web-2026-04-08-2130.md
     ```

**Non-compliant transcript** (insufficient — do not produce):
- File exists but contains only `## Session Summary` with no turn sections
- File contains only a list of attachments with no conversation content
- File is empty or a placeholder

**Close sequence** (in order):
1. Write the transcript markdown (turns + session summary)
2. Seed outstanding assertions
3. Atomic close: `cortex(tool="session_close", arguments='{"transcript_md": "<verbatim
   transcript markdown>", "session_summary_md": "<structural summary>",
   "agent": "claude-web", "session_id": "claude-web-YYYY-MM-DD-HHmm",
   ...}')` — creates transcript entity, journal row, and session edges in one call
4. Post session-close entry to agent-activity-journal (thread 480)
5. Report transcript ID and file path to the user"""

ASSERTION_SEARCH = """\
## Assertion Search (FTS5)
`cortex(tool="search", arguments='{"query": "...", "limit": 20}')` — fulltext search over assertions.
Indexes claim text + prospective_summary + flattened events + entity_id. Finds assertions by vocabulary NOT in the original claim (e.g. terms only in enrichment).
Optional: `entity_type` (filter to entity type), `superseded` (include superseded, default false).
Prefer `search` over `assertions` list when you have a natural-language query. Use `assertions` for exact entity_id / confidence filters."""

CORTEX_RETRIEVAL_WORKFLOWS = """\
## Retrieval Workflows (Cortex)

**Generic retrieval** (default for any Cortex query):
1. `cortex(tool="search", arguments='{"query": "…"}')` → hybrid FTS5+vector, CombMAX fusion
2. Extract entity_ids from results → `cortex(tool="activate", arguments='{"entity_ids": [...], "exclude_ids": [...]}')` for associative context
3. Rank by `entrenchment_score` (recency × access × confidence × derivation). Use `combmax_score` and `retrieval_source` for selection.
4. Check `cortex(tool="tag_resolve", …)` for pinned canonical assertions (`current` tag)

**Personal facts** (employment, financial, legal, medical):
1. ALWAYS search Cortex before answering — never use parametric knowledge for personal facts
2. Check temporal bounds (`valid_from`/`valid_until`) — assertions may have expired
3. Check supersession chains — only non-superseded assertions are current beliefs
4. For cross-entity topics, use `cortex(tool="activate", …)` or `cortex(tool="impact", …)` to traverse connections

**Belief revision** (new information contradicts existing):
1. Search for existing assertions on the entity
2. Use `cortex(tool="supersede", …)` — never just assert the new claim alongside the old
3. Write-path contradiction detection auto-flags cross-entity conflicts
4. For high-stakes domains (legal, financial), route to staging rather than auto-committing

**Temporal assertions** (dates, deadlines, employment periods, policy terms):
Use `valid_from` and `valid_until` when asserting time-bounded facts. An assertion without temporal bounds is treated as unbounded — valid indefinitely. Examples:
- Employment period: `valid_from="2020-01-15"`, `valid_until="2024-06-30"`
- Deadline: `valid_from="2026-04-09"`, `valid_until="2026-05-09"` (30-day window)
- Historical fact: `valid_from="2022-03-01"` (no end — still current)
Temporal bounds enable automatic expiry detection and prevent stale assertions from surfacing as current."""

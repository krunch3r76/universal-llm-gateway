# MCP Tool Reference

Detailed API docs for all primary MCP tools. Browse sections with
`fs(op="md_list", sandbox="workspaces", path="universal-llm-gateway/docs/tool-reference.md")` and read
individual tools with `fs(op="md_read", sandbox="workspaces", path="universal-llm-gateway/docs/tool-reference.md", section="<tool_name>")`.

## team_dispatch and frontier_dispatch

Two native-frontier MCP tools. `frontier_generate` and `team_generate` are **retired**
(Phase 4) — use these only.

For `op="generate"` and `op="to_thread"`, admission is async: returns
`{execution_id, pipeline, status, started_at}`; poll with `pipeline(op="result")`.
For `team_dispatch(op="handoff")` only: returns synchronously with
`{thread_id, to_agent, push_reminder, result_handle, poll_hint}` — **no**
`execution_id`; poll with `agent_bus(tool="wait", …)` from `poll_hint`.

| Tool | Use for | Required args | Role injection |
|---|---|---|---|
| `team_dispatch` | **API consult** (`op=generate\|to_thread`): `reviewer`, `gatherer`, `synthesizer`, `artisan`, `skeptic`. **Manual-seat handoff** (`op=handoff` only): `lead`, `cursor-lead`, `implementer`, seat slugs `claude-web` / `claude-cursor` — runtime-valid legacy `investigator` omitted here (see § below) | `op`, `role`; + `messages`, `dispatch_thread_id` for generate/to_thread; + `packet_path`, `subject` for handoff | yes (generate/to_thread); handoff resolves seat only — no model dispatch |
| `frontier_dispatch` | Raw provider-native call, no role | `op`, `model`, `messages` | no |

`op` values (`team_dispatch` and `frontier_dispatch`; frontier has no `handoff`):
- `"generate"` — direct mode; result content returned via `pipeline(op="result")`.
- `"to_thread"` — bus mode; Stargate posts the model's reply to `thread` on its behalf after dispatch completes.
- `"handoff"` (**team_dispatch only**) — manual-seat consult (`lead` → `claude-web`, `cursor-lead` / `implementer` → `claude-cursor`). Creates an agent-bus thread with a packet pointer synchronously. Returns `{thread_id, subject, to_agent, resolved_handoff_seat, handoff_contract, handoff_contract_source, push_reminder, result_handle, handoff_status, poll_hint}`. No model dispatch; web seats need operator push; Cursor seats need opening the thread in the IDE.

See `agent-skills/frontier-dispatch.md` § "Choosing direct vs bus mode" for decision rules.

### `team_dispatch`

Use for team role consults. Stargate resolves the role's default model,
enforces `allowed_models` / `allowed_options` from the `role:{slug}` Cortex
entity, assembles birth + briefing + continuation, and rejects violations before dispatch.

| Arg | Type | Description |
|---|---|---|
| `op` | `"generate"\|"to_thread"\|"handoff"` | Output channel |
| `role` | API (`generate`/`to_thread`): `reviewer`, `gatherer`, `synthesizer`, `artisan`, `skeptic`. Handoff only: `lead`, `cursor-lead`, `implementer`, `claude-web`, `claude-cursor` (and nicknames `web-claude`, `cursor-claude`) | Role slug or manual-seat alias |
| `messages` | `list[dict]` | Latest user turn only — prior turns assembled from server-owned thread. Unused by `op="handoff"`. |
| `dispatch_thread_id` | `str` | Compaction key for server-owned thread persistence (`thread:dispatch:{id}`). Stable per arc/session. Unused by `op="handoff"`. |
| `thread` | `str\|None` | Required when `op="to_thread"` — agent-bus thread ID |
| `subject` | `str\|None` | Bus reply subject (`to_thread`); required packet subject (`handoff`) |
| `model` | `str\|None` | Optional override; must be in persona's allowed set. Unused by `op="handoff"`. |
| `system` | `str\|None` | Extra caller-supplied system text appended during persona assembly |
| `reasoning_effort` | `str\|None` | Provider-native reasoning effort. Accepted values: `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`. Provider support varies (see `docs/thirdparty/{provider}/upstream` for the documented surface — e.g. OpenAI accepts `none/low/medium/high/xhigh`; Anthropic adaptive accepts `low/medium/high/xhigh/max`; Gemini 3 accepts `minimal/low/medium/high`). Unsupported values for the chosen model are dropped at the adapter layer with an INFO log. |
| `caller_agent` | `str\|None` | Dispatch provenance |
| `timeout_seconds` | `int\|None` | Pipeline wall-clock cap |
| `packet_path` | `str\|None` | `op="handoff"` only — workspaces-relative path to the pre-written six-block packet |
| `pointer_body` | `str\|None` | `op="handoff"` only — override the pointer turn body (≤25 lines) |
| `tags` | `list[str]\|None` | `op="handoff"` only — bus thread tags (default: `["agent:{to_agent}", "type:handoff", "contract:{handoff_contract}"]`). Caller-supplied tags are preserved; `contract:{value}` is appended if absent |
| `handoff_contract` | `"consult"\|"implement"\|None` | `op="handoff"` only — work intent. Omitted ⟹ inferred from role (`lead`/`cursor-lead`/web/cursor seats → `consult`; `implementer` → `implement`). Routing is unaffected. Conflicting pairs (`cursor-lead`+`implement`, `implementer`+`consult`) → 422 `handoff_contract_conflict` |

**`op="generate"` / `op="to_thread"` — admission guard for web/manual seats:**

Roles or seat slugs that resolve to a non-dispatchable profile (`dispatchable=false`,
e.g. `claude/web`, `grok/web`) are rejected **before** dispatch with 422
`web_seat_not_generate_target` — including when `model=` is supplied explicitly.
Valid generate roles: API-default roster slots (`reviewer`, `gatherer`,
`synthesizer`, `artisan`, `skeptic`). Invalid: seat slugs (`claude-web`, `web`),
web-default roles (`lead`, `investigator` (legacy)), and Cursor handoff-only roles
(`claude-cursor`, `cursor-lead`, `implementer`). Web Claude doing local file work should
use `fs` directly; peer consult → `frontier_dispatch` or an API role.

**`investigator` is legacy** (`role=investigator` → `grok-web`): a deep manual
grok-web research handoff, NOT the SuperGrok Heavy dispatch path. SuperHeavy uses
its own operator-driven workflow (`agent_skill:grok-web-dispatch` + connector
canary), not `team_dispatch(op=handoff, role=investigator)`. Do not list
`investigator` alongside `lead` / `cursor-lead` / `implementer` as a recommended
handoff target without the `(legacy)` marker.

**`op="handoff"` — manual-seat handoff primitive** (dispatching agent → web or Cursor IDE):

Operator shorthand **to `claude-web`** / **to `claude-cursor`** maps to this op (those seats
admit only handoff on `team_dispatch`). **Seat vs intent:** handoff routing resolves
`role` → seat only (`lead`/`claude-web` → claude-web; `cursor-lead`/`implementer`/`claude-cursor`
→ claude-cursor). Intent (consult vs bound implement) is **not** a separate routing axis.

| Intent | Web | Cursor |
|--------|-----|--------|
| Consult / dialectic | `role=lead` (or `claude-web`) | `role=cursor-lead` (or `claude-cursor`) |
| Bound implement (packet + acceptance criteria) | `role=lead` + implementer packet contract, or native `Pick up todo:{slug}` on web (no dispatch) | `role=implementer` — distinct from `cursor-lead` consult |
| Explicit contract | `handoff_contract=consult\|implement` on any handoff (optional; defaults from role) | same |

**Explicit `handoff_contract`** declares intent independent of role default. Final
contract = explicit if supplied, else role default (`lead`/`cursor-lead`/web/cursor
seats → `consult`; `implementer` → `implement`). It shapes the response echo
(`handoff_contract` + `handoff_contract_source`), the `contract:{value}` bus tag, and
the pointer `Contract:` line — **not** seat routing. Conflicting (role, contract) pairs
return 422 `handoff_contract_conflict`:

| role | + contract | result |
|---|---|---|
| `lead` / `claude-web` | `implement` | allow (web bound implement) |
| `cursor-lead` | `implement` | reject — use `role=implementer` or `handoff_contract=consult` |
| `implementer` | `consult` | reject — use `role=cursor-lead` or `handoff_contract=implement` |
| `claude-cursor` | `implement` | allow (docs recommend `implementer`) |

**Cursor → cursor** (`cursor-lead` → `claude-cursor`) is for **fresh perspective and tier upgrade**
in a new IDE thread (packet-booted context, operator picks Opus in the model picker) — reviews,
ongoing `project:` exploration, architecture, and extension work. **`implementer`** also resolves
to `claude-cursor` (handoff-only, generate → 422) but signals **packet-bound code execution**:
bound todo/spec + acceptance criteria + quality gates — not a reasoning consult. Web bound work
uses **`role=lead`** with the same implementer packet contract (or todo pickup without handoff).
See `projects/.cursor/rules/handoff-dispatchers.mdc` § `cursor-claude`; consult index
`agent-skills/consult-routing.md`.

Creates an agent-bus thread (e.g. `lead` → `claude-web`,
`cursor-lead` or `claude-cursor` → `claude-cursor`)
and returns `{thread_id, subject, to_agent, resolved_handoff_seat, handoff_contract,
handoff_contract_source, push_reminder, result_handle, handoff_status,
poll_hint}` synchronously — no model is dispatched and no `execution_id` is minted.
(`resolved_handoff_seat` aliases `to_agent`; `handoff_contract_source` is
`"explicit"` or `"role_default"`.)
`result_handle.kind` is `"agent_bus_thread"` (authoritative for retrieval — use
`agent_bus`, not `pipeline(op="result")`). Initial `handoff_status` is
`awaiting_first_reply`. `poll_hint` carries `tool` (`"wait"`), `arguments` (object,
human-readable), and `arguments_json` (string — **use this** for MCP `agent_bus`
calls; see `agent-skills/dispatch-shape.md`). Re-call with `wait_seconds` until
`status` is `complete`. Web seats start after the operator pushes the bus
message; Cursor seats start when the operator opens the thread in the IDE. The
endpoint enforces that the role resolves to a manual, non-dispatchable seat
(`delivery=manual, dispatchable=false`); dispatchable roles (reviewer, gatherer, etc.)
are rejected with `handoff_requires_web_seat` 422.

**Self-handoff:** a manual seat may call `op="handoff"` with `role` resolving to
**itself** (`lead`/`claude-web`, `cursor-lead`/`claude-cursor`) to open a new
agent-bus thread with packet-booted context. This is **supported** — distinct
from `op="generate"` to the same seat (422 `web_seat_not_generate_target`).
Authority: `projects/.cursor/rules/handoff-dispatchers.mdc` § Self-handoff;
`agent-skills/consult-routing.md`.

The pointer body defaults to the standard ≤25-line pointer template (see
`projects/.cursor/rules/handoff-dispatchers.mdc` and the durable packet skeleton
`docs/agent-guides/skills/handoff-packet-authoring.md`)
(packet path + six-block enumeration + reply instruction). Caller may supply
`pointer_body` override up to 25 lines. Longer overrides are rejected 422.

Caller **must** write the packet file before calling handoff; only a pointer is posted
to the bus. Operator push is still mandatory — `push_reminder` in the response carries
the formatted push instruction per `agent-bus-push-reminder_ws.mdc`.

**Canonical retrieval** (submit → handle → wait):

1. `team_dispatch(op="handoff", ...)` → read `result_handle`, `handoff_status`, `poll_hint`.
2. Surface `push_reminder` to the operator; wait for push.
3. `agent_bus(tool="wait", arguments=poll_hint.arguments_json)` — or build the same
   string from `poll_hint.arguments` / `result_handle` fields (`thread`, `after_turn`,
   `completion=first_reply_from`, `from_agent`).
   Re-call with `wait_seconds` 0 (snapshot) or up to 60 (server-side block) until
   `complete=true` and `status=complete`.

`agent_bus(tool="fetch")` is a **fallback** for manual inspection of thread turns —
not the primary handoff poll path.

**Anti-patterns** (handoff):

- Calling `pipeline(op="result", execution_id=...)` — handoff returns no `execution_id`.
- Polling the agent-bus "most recent thread" instead of the returned `thread_id`.
- Client-side MCP poll loops or Stargate wait proxies — use one `agent_bus(wait)` per check.
- Treating `model=` on `team_dispatch` as spawning a web session (web seats reject generate).

Examples:

```python
# Direct mode — result via pipeline(op="result")
team_dispatch(
    op="generate",
    role="gatherer",
    dispatch_thread_id="cursor-2026-06-02-design-review",
    messages=[{"role": "user", "content": "Review this design..."}],
    reasoning_effort="high",
    max_tool_turns=25,
    caller_agent="cursor",
)

# Bus mode — agent posts reply to thread 123
team_dispatch(
    op="to_thread",
    role="gatherer",
    dispatch_thread_id="cursor-2026-06-02-design-review",
    thread="123",
    subject="Design review",
    messages=[{"role": "user", "content": "Review this design..."}],
    reasoning_effort="high",
    max_tool_turns=25,
    caller_agent="cursor",
)

# Handoff mode — fresh-WEB dispatch to claude-web; operator push required
team_dispatch(op="handoff", role="lead",
              packet_path="universal-llm-gateway/tmp/reviews/<task>-claude-web-packet.md",
              subject="<Task> handoff — <subject>")
# → {thread_id, subject, to_agent: "claude-web", push_reminder,
#     result_handle, handoff_status: "awaiting_first_reply", poll_hint}
# poll_hint.tool == "wait"; poll_hint.arguments_json is the MCP wire form
agent_bus(tool="wait", arguments=poll_hint.arguments_json)  # not poll_hint.arguments (object)

# Equivalent literal:
agent_bus(tool="wait", arguments='{"thread": "<thread_id>", "after_turn": 1,
  "wait_seconds": 60, "completion": "first_reply_from", "from_agent": "claude-web"}')

# Handoff mode — dedicated Cursor thread (e.g. Opus); attend in IDE — no web push
team_dispatch(op="handoff", role="cursor-lead",
              packet_path="universal-llm-gateway/tmp/reviews/<task>-cursor-packet.md",
              subject="<Task> handoff — <subject>")
# → {to_agent: "claude-cursor", push_reminder mentions Cursor / agent-bus}
```

### `frontier_dispatch`

Use for raw persona-free provider-native calls. Caller supplies the model and any
system prompt.

| Arg | Type | Description |
|---|---|---|
| `op` | `"generate"\|"to_thread"` | Output channel |
| `messages` | `list[dict]` | Conversation messages |
| `model` | `str` | Required provider-qualified model ID, e.g. `openai/gpt-5.4` |
| `thread` | `str\|None` | Required when `op="to_thread"` — agent-bus thread ID |
| `subject` | `str\|None` | Bus reply subject (bus mode only) |
| `system` | `str\|None` | Caller-supplied system prompt |
| `reasoning_effort` | `str\|None` | Provider-native reasoning effort. Accepted values: `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`. Provider support varies (see `docs/thirdparty/{provider}/upstream` for the documented surface). Unsupported values for the chosen model are dropped at the adapter layer with an INFO log. |
| `generation_options` | `dict\|None` | Pass-through provider generation parameters |
| `max_tool_turns` | `int\|None` | Tool-loop turn cap |
| `transcript_id` | `str\|None` | Optional continuation identifier |
| `caller_agent` | `str\|None` | Dispatch provenance |
| `timeout_seconds` | `int\|None` | Pipeline wall-clock cap |

Examples:

```python
# Direct mode
frontier_dispatch(
    op="generate",
    model="openai/gpt-5.4",
    messages=[{"role": "user", "content": "Summarize..."}],
    system="You are a concise summarizer.",
    reasoning_effort="high",
)

# Bus mode — raw model posts reply to thread 456
frontier_dispatch(
    op="to_thread",
    model="openai/gpt-5.4",
    thread="456",
    subject="Summary",
    messages=[{"role": "user", "content": "Summarize..."}],
)
```

Chat-Completions-only OpenAI search models (`openai/*-search-api`) are rejected
on both dispatch tools. Use `llm_generate` for those.

## panel_dispatch

Consensus panel helper — the **default transport for ≥2-family material decisions**
(hard triggers in `consensus-steelman-posture` §1). Fans out `skeptic` + `reviewer`
(optional `synthesizer`) via `team_dispatch` admission; returns `panel_executions`
for Menu D asserts. **Always steelman live options first**; run the panel when the
material gate fires — not only on explicit operator request. Read
`agent-skills/consensus-steelman-posture.md` before use. Adjudicating-caller review +
`panel_adjudication_artifact` remain NON-offloadable after the helper returns.

| Arg | Type | Description |
|---|---|---|
| `messages` | `list[dict]` | Latest user turn(s) per member |
| `dispatch_thread_id` | `str` | Server-owned compaction key (required) |
| `disposition` | `"panel"` | Must be `panel` |
| `include_synthesizer` | `bool` | Optional gemini tiebreaker |
| `poll` | `bool` | Block-poll each `execution_id` when true |

`extra_members` is a **library-only** hook on `agent_seat.panel_dispatch.resolve_panel_members`;
the MCP `panel_dispatch` tool intentionally does not expose it — the MCP surface stays a
fixed roster (`skeptic` + `reviewer` [+ `synthesizer`]).

The legacy `agent_consult` overflow tool was removed (2026-06); use
`panel_dispatch` for ≥2-provider panels or explicit `team_dispatch` per role.

## dispatch

Call any non-primary tool by name. The advertised MCP catalog is intentionally
lean (cortex, agent_bus, fs, dispatch, tool_search, retrieve); everything else
lives in the overflow registry and is invoked through `dispatch`.

Discovery flow: use `tool_search(query="...")` first to obtain the tool name
and a ready-to-paste `dispatch_template`, then call `dispatch` with that
template. The model holds the returned template in working memory across
subsequent turns — no need to re-search the same tool.

### Dispatchable tools

The demoted set covers pipelines, dispatch surfaces (team/frontier/grok),
service control (`manage`), observability/debugging, data (`sql`, `rag`,
`web_fetch`, `web_search`), session boot (`cortex_boot`, `boot_inspect`),
code quality (`quality_gate`), private domain tools (`tools.local/`), plus
low-level filesystem helpers used internally by the `fs` wrapper. Use
`tool_search` to enumerate — the manifest is auto-derived at startup and
doesn't drift.

### Example

```
tool_search(query="restart service")
# → dispatch(tool="manage", arguments='{"action": "<value>", ...}')
dispatch(tool="manage", arguments='{"action": "sync_restart", "service": "mcp"}')

tool_search(query="poll pipeline result")
dispatch(tool="pipeline", arguments='{"op": "result", "execution_id": "...", "wait_seconds": 60}')
```

## rag_search

**Sole primary MCP/agent surface** for RAG. Returns raw context chunks with
source labels for the agent to cite, gate (lawyer-stance), reason over, and
synthesize. **Agents must use this exclusively**; `rag_answer` (and `rag(op="answer")`)
is buried in MCP and reserved exclusively for debugging the rag-answer* pipelines
(via direct dispatch or Stargate /v1/chat/completions). `rag_search_preview` provides
bounded snippets for Cursor UIs.

### Surface hierarchy
- `rag_search` / `rag(op="search")`: only agent surface.
- `rag_search_preview`: bounded exploration (5 chunks default, truncated snippets; follow up with `rag_get_chunks`).

### Query language

Queries **must be natural language**. Boolean operators do not work — the pipeline
uses embedding-based (semantic) retrieval. A query like
`steelmanning OR "intellectual courage" OR "radical honesty"` gets embedded as a
single vector; the embedding model treats `OR` as a literal word, producing a
muddled average that degrades the primary retrieval signal.

**Instead:**
- **Multi-agent mode:** run one `rag_search` call per concept in parallel (one per
  sub-agent). Each gets a clean embedding and results are synthesized by the caller.
- **Single-agent mode:** phrase it as a natural language question covering all concepts
  (`"What does the research say about steelmanning, intellectual courage, and radical
  honesty?"`). The internal query rewriter and sparse retrieval pool handle
  decomposition.

### Args

| Arg | Type | Description |
|---|---|---|
| `query` | str | Natural language search query — REQUIRED |
| `top_k` | int | Max chunks after RRF merge (default 20; use 25-30 for exploration, 5-10 for focused lookups) |
| `limit` | int\|None | Alias for `top_k` (ergonomics for MCP/dispatch paths; error on conflict) |
| `scope` | str\|list\|None | Named scope filter: single string, comma-separated string, or list. Call `rag_list_scopes()` for valid names. |
| `prefix` | str\|list\|None | Source path prefix filter. Mutually exclusive with `scope`. |

### Returns

```
{"status": "ok", "pipeline": "rag-context", "content_length": <int>,
 "duration_s": <float>, "context": "<assembled context with source labels>"}
```
On error: `{"error": "<message>"}`


### Args

| Arg | Type | Description |
|---|---|---|

## cortex

Cortex knowledge system — entities, assertions, relationships, edges, journals.

### Operations

| Op | Args | Description |
|---|---|---|
| `entities` | type?, limit? | List entities |
| `entity_get` | entity_id, include_edges?, edge_limit? | Get entity with assertions + relationships. Pass `include_edges=true` to also return `reasoning_edges` (active session edges). `edge_limit` defaults to 20. |
| `entity_create` | id, type, name, description?, workflow_state?, notes?, aliases?, attributes?, source_uri?, content_hash? | Create entity (409 if exists). Option-C traits (`confidence_band`, `lifecycle`, `adoption`) are derived at birth — set via `entity_update` after create (422 if passed on create). |
| `entity_update` | entity_id, name?, description?, workflow_state?, confidence_band?, lifecycle?, adoption?, notes?, aliases?, attributes?, source_uri?, content_hash? | Update mutable fields. Trait write surface: `confidence_band`, `lifecycle`, `adoption`. null clears; omit leaves untouched. |
| `assertions` | entity_id?, confidence?, review_status?, superseded?, limit? | List assertions. review_status: committed/flagged/staged/rejected |
| `assert` | entity_id, claim, confidence, evidence, evidence_uris?, seeded_by?, derivation_type?, confidence_score?, observed_at?, valid_from?, chunk_id? | Direct write. confidence: confirmed/believed/suspected/hypothesized. derivation_type: quotation/compression/inference/other |
| `assertion_update` | assertion_id, superseded_by?, valid_until?, confidence?, confidence_score?, review_status?, reviewer?, reviewed_at? | Update assertion metadata |
| `supersede` | old_assertion_id, entity_id, claim, confidence, evidence, session_id, agent, evidence_uris?, valid_from?, derivation_type? | Atomic close-old + create-new. Also auto-creates the `supersedes` edge in the same transaction. |
| `relationships` | entity_id?, type_id?, limit? | List with names, strength |
| `relationship_create` | source_id, target_id, type_id, role?, strength?, evidence?, chunk_id?, valid_from?, valid_until?, source_uri?, session_id?, agent? | Create structural relationship. Pass `session_id` + `agent` for provenance (nullable; recommended for new writes). |
| `stats` | — | Dashboard counts |
| `search` | query, limit?, superseded?, entity_type?, intent? | FTS5 hybrid search over assertions. `intent`=`summary` (default): compact hits; `intent`=`full`: detail rows with enrichment fields. Prefer over `assertions` for natural-language queries |
| `impact` | entity_id, depth? | Transitive reverse-dependency BFS from entity. Returns `{seed_entity, depth, impacted_entities: [{entity_id, entity_name, hop_distance, path_trace, assertion_count, edge_types, substrates}], total_impacted_assertions}`. Walks both substrates via `_DEPENDENCY_EDGE_TYPES` (`requires`, `depends_on`, `derived_from`, `evidence_for`, `extends`). |
| `activate` | entity_ids, depth?, max_results?, exclude_ids?, suppress_hubs?, decay_factor? | Spreading activation from seed entities. Returns `{seed_entities, depth, hub_suppression, count, activated: [{assertion_id, entity_id, claim, confidence, entrenchment_score, activation_score, hop_distance, activation_path, edge_types_traversed, substrates_traversed}]}`. Walks both substrates via the full 15-type association set. `entity_ids` is comma-separated. |
| `journal_read` | limit? | Recent session journals |
| `journal_write` | timestamp, agent, summary, domains?, decisions?, open_items?, entity_ids?, file_path? | Write journal |
| `review_queue` | limit? | Provisional entities + flagged assertions + low-confidence + thin descriptions |
| `edge_create` | session_id, agent, from_node, to_node, edge_type, strength?, edge_source?, context?, prompt?, seeded_by?, metadata? | Seed reasoning connection |
| `edges` | from_node?, to_node?, edge_type?, agent?, session_id?, include_retired?, limit? | Query edges |
| `edge_traverse` | node, hops?, edge_type?, min_strength? | Graph traversal (1–2 hops) |
| `edge_retire` | edge_id, valid_until? | Retire an edge |
| `edge_types` | — | List registered edge types |

#### `cortex(tool="search")` — `intent` projections

`intent=summary` intentionally omits `session_tag` and `evidence`. List-compact (`GET /assertions?compact=true`) derives `session_tag` from evidence for boot briefing only (`briefing_card.py`); search summary is a distinct projection, not a parity clone of list-compact. Use `intent=full` when raw `evidence` is needed. Add `session_tag` to the summary projection only when a real search-summary consumer requires session prefixes.

### Example

```
cortex(tool="entities", arguments='{"type": "person", "limit": 20}')
cortex(tool="assert", arguments='{"entity_id": "person:foo", "claim": "...", "confidence": "confirmed", "evidence": "..."}')
```

## agent_bus

Inter-agent message bus — threads, turns, read/reply coordination.

### Operations

| Op | Args | Description |
|---|---|---|
| `threads` | status?, to?, limit? | List threads. status: active/archived/all |
| `fetch` | thread, last?, compact?, mark_read? | Get turns from a thread (fallback / inspection). compact=true strips markdown. For handoff completion use `wait`, not fetch loops. |
| `wait` | thread, after_turn?, wait_seconds?, completion?, from_agent? | **Canonical handoff retrieval** — server-side short-block until the consult posts a **bus turn** after the pointer (`completion=first_reply_from` + canonical `from_agent`; alias-aware). `wait_seconds` clamped ≤60 (0=snapshot). Returns `{complete, status, push_required, suggested_next, qualifying_reply_turn, thread_status, ...}`. When `complete=true` and `thread_status=active`, `suggested_next` is an object (`phase=consult_turn_posted`, `consult_turn`, `steps`: fetch → apply → close) — not arc completion. Re-call to poll — one HTTP call per invocation. |
| `get` | thread, turn_number | Get one specific turn |
| `post` | slug, to, subject, body, from_agent, tags? | Start a new thread |
| `reply` | thread, to, subject, body, after_turn?, from_agent | Reply to a thread |
| `read` | thread, turn_number | Mark a turn as read |
| `archive` | thread | Archive a thread |
| `summary` | — | Unread counts per agent |

### Example

```
agent_bus(tool="fetch", arguments='{"thread": "111", "last": 3, "compact": true}')
agent_bus(tool="wait", arguments='{"thread": "111", "after_turn": 1, "wait_seconds": 30, "completion": "first_reply_from", "from_agent": "claude-web"}')
agent_bus(tool="post", arguments='{"slug": "review-bug", "to": "cursor", "subject": "Bug found", "body": "## Details\n...", "from_agent": "grok"}')
```

## observability

Query system telemetry, traces, and request snapshots from the Event Service.

### Operations

| Op | Params | Description |
|---|---|---|
| `recent-failures` | limit? | Failures/errors in current session |
| `noise-profile` | minutes? | Signal frequency histogram |
| `coordination-audit` | — | Recent role=coordination events |
| `model-timeline` | model_id | Load/execute/unload for a model |
| `request-trace` | request_id | All events for a request |
| `request-lifecycle` | request_id | Snapshot phases for a request |
| `request-summary` | — | Aggregate request stats |
| `pipeline-trace` | execution_id | Step-by-step execution trace |
| `compare-runs` | run_a, run_b | Side-by-side metrics |
| `federation-health` | — | Latest relay telemetry |
| `capacity-snapshot` | — | Current slot usage |
| `signal-events` | signal? | Recent events for a signal pattern |
| `stack-last-started` | — | Per-service last startup timestamp |
| `realtime-snapshot` | — | Last N from in-memory ring buffer |
| `operations` | — | List all available operations |
| `raw_sql` | sql, params?, limit? | Raw SQL SELECT query |

### Example

```
observability(operation="recent-failures", params={"limit": 20})
observability(operation="pipeline-trace", params={"execution_id": "abc123"})
```

## fs

Unified file operations across sandboxes: `cortex` (/data/files), `workspaces` (repo root at /mnt/torus/projects/). `workspaces` paths MUST include the repo name prefix (e.g. `universal-llm-gateway/…`). Use `op="list"` for directories.

Both `sandbox` and `op` are REQUIRED.

For project navigation, `list` returns both `files` and `directories`. Read the
`directories` field when you need directory-aware navigation, including empty or
untracked directories.

`read` is intentionally unified across sandboxes. In text mode it supports
source files plus structured document formats such as PDF, DOCX, ODT, EML, and
HTML. Use `binary=true` only for true byte-oriented workflows such as OCR,
binary ingest, or image/model transfer between tools.

### Standard ops

| Op | Required args | Description |
|---|---|---|
| `read` | path | Read file |
| `read_multi` | paths (array) | Read multiple files |
| `write` | path, content | Write/create file |
| `append` | path, content | Append to file |
| `prepend` | path, content | Prepend to file |
| `replace` | path, target, content, all_occurrences? | Replace text |
| `insert_at_line` | path, content, line | Insert at line number |
| `list` | path? | List directory (defaults to sandbox root) |
| `delete` | path | Delete file |
| `search` | path (file or dir), content (regex) | Regex search (cortex + workspaces) |

#### `fs(op="search")` — regex search (both sandboxes)

Conversion-aware regex search over text and converted documents
(PDF/DOCX/ODT/EML/HTML). PDFs are loaded sidecar-first
(`decision:mcp-fs-timeout-observability`). `path` may be a file or a directory.

Response envelope (single shape, `mode` discriminator):

```json
{
  "path": "<requested path>",
  "mode": "file" | "directory",
  "matches": [
    {"file": "<rel path, directory mode only>", "line": 42, "text": "..."}
  ],
  "truncated": false,
  "skipped_converted": 0,
  "extraction_method": "sidecar_markdown | pymupdf_plaintext | converted | native_text"
}
```

Directory search bounds converted-file extraction by an aggregate wall-clock
budget and a converted-file cap; files beyond either bound increment
`skipped_converted`. Truly-binary files are skipped (`_BINARY_SUFFIXES`
unchanged — converted ≠ truly-binary, narrowing decision:mcp-list-include-binary-paths).

### Markdown section ops (for large docs >5k chars)

PDF, DOCX, ODT, and EML files are auto-converted to markdown for read ops
(`md_list`, `md_read`). Write ops (`md_replace`, `md_append`, `md_delete`)
work only on natively text files — converted formats are rejected.

| Op | Required args | Description |
|---|---|---|
| `md_list` | path | List sections (works on PDF/DOCX/ODT/EML too) |
| `md_read` | path, section | Read section (works on PDF/DOCX/ODT/EML too) |
| `md_replace` | path, section, content | Replace section (text files only) |
| `md_append` | path, section, content | Append to section (text files only) |
| `md_delete` | path, section | Delete section (text files only) |

## pipeline

Unified pipeline execution and inspection tool. Dispatches by `op` to one of
four handlers (`run`, `async`, `result`, `validate`). Replaces the former
separate `pipeline`, `pipeline_async`, `pipeline_result`, and `validate_pipeline`
tools.

YAML, prompts, and model configs hot-reload on file change. `op="run"` HTTP
timeout is auto-detected from the pipeline's configured `timeout_seconds`.

### Args

| Arg | Type | Description |
|---|---|---|
| `op` | `"run"`\|`"async"`\|`"result"`\|`"validate"` | Operation selector — REQUIRED |
| `pipeline_id` | str\|None | Pipeline ID (e.g. `consensus`, `rag-context`, `gatherer-dispatch`). Required for `run`, `async`, `validate`. |
| `messages` | list[dict]\|None | Chat messages in OpenAI format. Required for `run`, `async`. |
| `execution_id` | str\|None | Async execution ID. Required for `result`. |
| `options` | dict\|None | Optional `pipeline_options` (run/async) |
| `timeout` | float\|None | Override HTTP timeout for `run` (auto-detected when omitted) |
| `result_delivery` | dict\|None | Async bus-delivery hook (phase 2): `{bus_thread, bus_from_agent, bus_to_agent, bus_subject}` |
| `wait_seconds` | float | Server-side short-poll window for `result` (0 = immediate; clamped to 60s at Stargate) |

### Ops

| Op | Required | Returns |
|---|---|---|
| `run` | `pipeline_id`, `messages` | `{content, model, duration_s, execution_id?, usage?}` — blocks until pipeline completes |
| `async` | `pipeline_id`, `messages` | `{execution_id, pipeline, started_at, status}` — fire-and-forget; use for calls expected to exceed the MCP 300s read-timeout ceiling |
| `result` | `execution_id` | Tracker record: `{execution_id, pipeline, status, started_at, completed_at, result, error}` |
| `validate` | `pipeline_id` | `{valid, pipeline, steps, models, domain, errors}` — no inference compute consumed |

### Examples

```
pipeline(op="validate", pipeline_id="gatherer-dispatch")
pipeline(op="run", pipeline_id="rag-context",
         messages=[{"role":"user","content":"..."}])
pipeline(op="async", pipeline_id="gatherer-dispatch",
         messages=[{"role":"user","content":"..."}])
pipeline(op="result", execution_id="<id>", wait_seconds=60)
```

## manage

Service lifecycle — start, stop, rebuild, restart, health checks.

### Actions

| Action | Service needed? | Description |
|---|---|---|
| `status` | No | Running/stopped for all services |
| `health` | Yes | Health detail for one service |
| `start` | Yes | Start a stopped service |
| `stop` | Yes | Stop a running service |
| `restart` | Yes | Stop then start |
| `rebuild` | Yes | Rebuild container image and restart |
| `wait_healthy` | Yes | Block until RUNNING or timeout |

Services: gateway, stargate, rag, cloud_proxy, mcp, event_service, cortex_api, agent_bus

### Post-code-change workflow

1. `quality_gate(files=[...])`
2. `manage(action="rebuild", service="gateway")`
3. `manage(action="wait_healthy", service="gateway", timeout=120)`
4. `pipeline(...)`

## model_status

Query model load/busy/loading status across all Stargate nodes.

- Without `model_id`: all models with per-model placement
- With `model_id`: detail for one model (404 → `{"error": "Model not found: ..."}`)

### Args

| Arg | Description |
|---|---|
| `model_id` | Optional specific model. Omit for all. |
| `status_filter` | Optional: loaded, busy, loading, available (all-models only) |

## cortex_boot

Unified boot briefing for session start. Persona-scoped: each agent gets tailored content.

### Args

| Arg | Default | Description |
|---|---|---|
| `agent` | `web` | Agent profile: web, cursor, api, grok, subagent |

### Response fields (key selection)

| Field | Description |
|---|---|
| `session_id` | Server-minted session ID in format `{agent}-{YYYY-MM-DD}-{HHMM}` UTC. Hold in working memory; pass to all `edge_create` calls for the session duration. |
| `boot_narrative` | Rendered Markdown briefing (salience sections, todos, threads, temporal). |
| `continuation_state` | Recent decisions, service observations, open todos. |
| `agent_bus` | Active threads and unread turns. |
| `temporal` | Active and upcoming temporally-bounded assertions. |
| `injected_artifacts` | List of `InjectedArtifact` objects — every byte-bearing source that reaches the agent's context. Each carries: `name`, `mode` (`inline`/`written_file`/`manifest_only`/`auto_postfile`), `source` (function or file path), `bytes` (rendered byte count; `0` for `manifest_only` — not yet fetched), `sha256` (raw bytes, no canonicalization), `path` (filesystem path if `mode == "written_file"`; `null` for `mode == "inline"` — including the same `operational_context` artifact name which appears as `written_file` under LIVE boots and `inline` under `boot_inspect`), `fetches` (list of `FetchRecord` provenance entries). `FetchRecord.bytes` is `-1` (`BYTES_UNAVAILABLE`) when the recorder could not serialize the fetch result — means measurement unavailable, not content absent; `mcp.cortex.boot.fetch.failed` event also fires. |
| `audit_dump_path` | Path to the per-boot audit dump written to `/data/files/notes/system/audit/boots/` on each call; filename uses second-resolution timestamp (`{agent}-YYYY-MM-DD-HHMMSS.md`) decoupled from `session_id` (minute-resolution) for write-uniqueness within the same minute. `null` if the dump write failed (best-effort; boot still succeeds). Indexed under RAG scope `boot_snapshots` for historical drift queries. |

## boot_inspect

Read-only inspection surface for boot payload auditing and profile diffs.

`boot_inspect` runs the same fetch/render graph as `cortex_boot`, but in inspect
mode (`mode="inspect"`): no operational-context file write, no audit dump file
write, and no `mcp.cortex.boot*` event emission.

### Args

| Arg | Default | Description |
|---|---|---|
| `agent` | `cursor` | Primary agent profile to inspect |
| `transcript_id` | `""` | Optional continuation transcript for primary profile |
| `diff_with` | `""` | Optional secondary agent profile; when provided returns `primary`, `secondary`, and `diff` |

### Response fields (key selection)

| Field | Description |
|---|---|
| `mode` | Always `inspect` |
| `operational_context_inline` | Full rendered operational context text (inline, never written in inspect mode) |
| `operational_context_ref` | Always `null` in inspect mode |
| `audit_dump_path` | Always `null` in inspect mode |
| `injected_artifacts` | Same manifest schema as `cortex_boot`; `operational_context` artifact is `mode: inline` in inspect mode |
| `diff` | Present only when `diff_with` is set. Contains `artifacts_only_in_primary`, `artifacts_only_in_secondary`, and `artifacts_with_delta` (`kind: inline_canonical_text` or `sha256_mismatch`) |

## rag

RAG knowledge retrieval and index management.

### Operations

| Op | Args | Description |
|---|---|---|
| `search` | query (REQUIRED), scope?, prefix?, top_k? | Semantic search. scope and prefix are mutually exclusive. |
| `answer` | question (REQUIRED), scope?, prefix?, deep? | RAG-grounded answer. deep=True for iterative retrieval. |
| `list_scopes` | — | List scopes with prefixes and coverage |
| `coverage` | — | Per-scope indexed file counts |
| `upsert_article` | url (REQUIRED), title?, scope? | Index article |
| `delete_source` | source_hash (REQUIRED) | Delete indexed source |
| `refresh_hints` | scope? | Regenerate discriminative vocabulary hints |
| `orphaned_articles` | — | Find articles not in any scope |
| `delete_directory` | directory (REQUIRED) | Delete all indexed content under a path |

## quality_gate

Run ruff lint + compileall on specified files. Use after modifying code.

### Args

- `files`: list of file paths relative to project root

### Returns

`{"passed": true/false, "ruff": {...}, "compile": {...}}`

## retrieve

Retrieve a stored oversized tool response by reference ID.

When a tool response exceeds the byte threshold, the guard stores it and returns
a compact reference. Call `retrieve(id="rs_...")` to claim the full result.
Pop semantics: one retrieval per stored response. Expires after 10 minutes.

## web_search

Search the web via Brave Search API. Returns results with title, URL, snippet, and age.

### Args

- `query`: search string (REQUIRED)
- `max_results`: 1–20, default 5

## sql

Read-only SELECT queries against configured SQLite databases.

### Args

- `sql`: SELECT statement (REQUIRED)
- `db`: database name (default `"default"`)
- `params`: bind parameters list

## Relay Deployment (proactive socket dir)

**Proactive pre-creation step** added to deploy scripts (`relay.py:_run_start()` calls `ensure_relay_dirs()` / `ensure_socket_dir()` *first*, before any `docker compose` or stop).

- Prevents Docker daemon from creating `/tmp/universal-protocol` as `root:root` (the exact race that made `relay-jupiter` unreachable).
- Uses sudo-free Docker one-off (`alpine:3` with `-v` mount + `rm -rf && mkdir && chmod 777`) in `_recover_root_owned_socket_dir()`.
- `ensure_bind_mount_dirs()` does analogous pre-creation for `tmp/gpu-nodes/*` runtime dirs.
- Topology panel diagnostic: new `socket_dir_root_owned` status reason.

See `scripts/model_manager/ui/controller/service_config.py:ensure_socket_dir()`, `relay.py`, and `systems/federation/REFERENCE.md` (Unix socket invariants for Relay ↔ Edge).

This eliminates the need for manual `sudo rm -rf /tmp/universal-protocol`.

## tool_search

Runtime discovery for non-primary MCP tools. The advertised catalog is
intentionally lean — only `cortex`, `agent_bus`, `fs`, `dispatch`,
`tool_search`, and `retrieve` are primary tools. Every other tool is
reachable via `tool_search(query="...")` → `dispatch(tool="<name>", arguments='...')`.

### Args

- `query: str` — keywords matching the operation you want (e.g. `"restart service"`,
  `"poll pipeline"`, `"raw sql"`, `"query events"`)
- `limit: int = 5` — max number of results returned

### Response shape

```json
{
  "query": "poll pipeline result",
  "results": [
    {
      "name": "pipeline",
      "purpose": "Pipeline execution and inspection — run/async/result/...",
      "ops": ["run", "async", "result", "validate", "stats", "cancel"],
      "dispatch_template": "dispatch(tool=\"pipeline\", arguments='{\"op\": \"<op>\", ...}')",
      "required_args_by_op": {"result": ["execution_id"], ...},
      "example": "dispatch(tool=\"pipeline\", arguments='{\"op\": \"result\", \"execution_id\": \"...\"}')"
    }
  ],
  "total_matches": 1,
  "_next": "Call dispatch with the template — do not re-search unless the result is clearly wrong."
}
```

When the query matches no manifest entry, the response includes
`available_tools_summary` (full name + purpose list) so the model has an
inline fallback catalog without making another call.

### Manifest source

Auto-derived at server startup from the overflow registry produced by
`_prune_to_primary` plus any inline wrappers demoted via
`_demote_inline_wrappers`. There is no static manifest file — the description,
ops list, and dispatch template come from each tool's docstring +
`inputSchema`. Adding a new dispatched tool requires no manifest update.

### Failure modes & rollback signals

- `mcp.tool.search.miss` event rate > 10% → search ranking is degraded
- `mcp.tool.dispatch.unknown` rate > 2× baseline → model is hallucinating
  tool names; demoted set may be too aggressive
- Two consecutive `tool_search` calls in the same turn → search-loop
  signal; the response's `_next` hint normally prevents this

See `tasks/discoveries/mcp-tool-definition-context-churn.md` § Q10 for
the full rollback decision matrix.

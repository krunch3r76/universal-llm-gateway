# MCP Tool Reference

Detailed API docs for all primary MCP tools. Browse sections with
`fs(op="md_list", sandbox="workspaces", path="universal-llm-gateway/docs/tool-reference.md")` and read
individual tools with `fs(op="md_read", sandbox="workspaces", path="universal-llm-gateway/docs/tool-reference.md", section="<tool_name>")`.

## Dispatch-style arguments

`cortex`, `agent_bus`, `agent_bus_read`, `rag`, and `dispatch` are **dispatch-style**
tools: an outer selector (`tool` or `op`) plus an inner **`arguments` JSON-encoded
object string** — e.g. `cortex(tool="entity_get", arguments='{"entity_id": "decision:foo"}')`.

The inner `arguments` is declared `type: string` **on purpose** and does not accept
a bare object. Claude.ai's MCP client silently drops optional params with
`anyOf`/`object` JSON Schema (`mcp-tool-param-types` invariant), so a union/object
`arguments` schema would make the param invisible on the primary client. This is a
ratified decision — see `decision:dispatch-arguments-string-wire-form`.

### Large or quote-heavy payloads

A failed parse (`arguments must be a JSON-encoded object string …`) is almost always
an **escaping** failure: a large payload with embedded `"`, newlines, or JSON/code
fences was hand-built into the JSON string and mis-escaped (e.g. a `session_close`
`transcript_md` or `handoff_prompt`). Do **not** re-escape by hand. Instead:

- Write the payload to a file and pass a **file-path parameter** read server-side:
  `session_close` accepts `transcript_jsonl_path`, `handoff_source_path`, and
  `source_ref` in place of inline `transcript_md` / `handoff_prompt`.
- Or use the `/agent-bus` (`scripts/agent-bus`) **direct-UDS CLI**, which bypasses
  MCP shape validation entirely.

For `team_dispatch(op="handoff")` poll loops, keep using `poll_hint.arguments_json`
(the correctly-serialized wire form) rather than `poll_hint.arguments` (the
human-readable object).

The canonical session-close trigger is a `handoff_prompt` that embeds a
`poll_hint` snippet. A continuation handoff should not carry raw poll-hint JSON at all
(see the session-close-handoff skill — handoff ≠ dispatch); when any embedded JSON/quotes
are present, pass `handoff_source_path` from the start rather than as failure recovery.
See `agent-surface/sources/session-close-handoff.md`.

## team_dispatch

Sole agent-facing dispatch MCP tool. `frontier_generate`, `team_generate`,
`frontier_dispatch`, `dispatch_frontier`, and `dispatch_team` are **retired**
(Phase 4/5) — use `team_dispatch` only.

For `op="generate"` (API functional roles) and `op="to_thread"`, admission is
async: returns `{execution_id, thread_id, poll_hint, output_contract=thread, …}`.
**Default:** `op="generate"` auto-provisions an agent-bus thread; poll
`agent_bus(tool="wait", …)` from `poll_hint` (primary). `pipeline(op="result")`
remains available for execution metadata/content. Reasoning transparency is
returned as `knob_resolution`; inspect `status`, `parity`, and `notes`.
For `team_dispatch(op="handoff")` only: returns synchronously with
`{thread_id, to_agent, push_reminder, result_handle, poll_hint}` — **no**
`execution_id`; poll with `agent_bus(tool="wait", …)` from `poll_hint`.

| Tool | Use for | Required args | Role injection |
|---|---|---|---|
| `team_dispatch` | **API consult** (`op=generate\|to_thread`): `reviewer`, `gatherer`, `synthesizer`, `artisan`, `skeptic` (+ optional `model=` within `allowed_models`). **Manual-seat handoff** (`op=handoff` only): `web-consult`, `web-implement`, `cursor-consult`, `cursor-implement` | `op`, `role`, `contract`, `dispatch_thread_id` for generate/to_thread; + `subject` and **at least one of** `source_ref` \| `packet_path` for handoff | yes (generate/to_thread); handoff resolves seat only — no model dispatch |

`op` values (`team_dispatch`):
- `"generate"` — **default bus mode** for API roles: auto-provisions thread +
  `output_contract=thread`; poll `poll_hint` (agent-bus wait). `cursor-sdk` uses
  the dedicated SDK orchestrator (same bus default). **`role=cursor-sdk` is the
  default transport for bound mechanical implement** (`source_ref=todo:{slug}` +
  `contract=implement`, auto Composer, no IDE pickup) — the `cursor-implement`
  handoff is the operator-attended fallback. Materialization reads todo attributes;
  the implement materialized packet MUST be dense (Composer executes mechanically);
  a determinate, pre-authored task may instead run via `contract=light-bounded` or
  `contract=pure-mechanical` with context on `dispatch_thread_id` (no packet, still
  explicit + bounded — § General execution lane). Legacy: `packet_path` +
  `contract=implement`. See `agent-skills/consult-routing.md` § Implement lane — source_ref.
- `"to_thread"` — bus mode when caller already owns `thread`; Stargate posts the
  role's reply on its behalf after dispatch completes.
- `"handoff"` (**team_dispatch only**) — manual-seat handoff (`web-consult` → `claude-web`, `web-implement` → `claude-web` (bound implement), `cursor-consult` / `cursor-implement` → `claude-cursor`). Creates an agent-bus thread with a packet pointer synchronously. Returns `{thread_id, subject, to_agent, resolved_handoff_seat, handoff_contract, handoff_contract_source, push_reminder, result_handle, handoff_status, poll_hint}`. No model dispatch; web seats need operator push; Cursor seats need opening the thread in the IDE.

See `agent-skills/frontier-dispatch.md` § "Choosing direct vs bus mode" for decision rules.

### `team_dispatch`

Use for team role consults. Stargate resolves the role's default model,
enforces `allowed_models` / `allowed_options` from the `role:{slug}` Cortex
entity, assembles birth + briefing + continuation, and rejects violations before dispatch.

| Arg | Type | Description |
|---|---|---|
| `op` | `"generate"\|"to_thread"\|"handoff"` | Output channel |
| `role` | API (`generate`/`to_thread`): `reviewer`, `gatherer`, `synthesizer`, `artisan`, `skeptic`, `cursor-sdk`. Handoff only: `web-consult`, `web-implement`, `cursor-consult`, `cursor-implement` | `{platform}-{contract}` roster slug (seat aliases like `claude-web` → 422 `handoff_role_invalid`). **`skeptic`**: default `xai/grok-4.20-multi-agent-0309` is inline-only (no client-side MCP) — pre-stage context on `dispatch_thread_id`; admission returns `capabilities.inline_only` / `capabilities.mcp_enabled`. |
| `contract` | `"light-bounded"\|"pure-mechanical"\|"implement"\|"wrap"` | **Required** for `op="generate"`/`op="to_thread"`. Authority grant: `light-bounded` (bounded consult/execution), `pure-mechanical` (deterministic write loop), `implement` (bound mechanical implement — default via `source_ref=todo:{slug}` server materialization on `role=cursor-sdk`; legacy `packet_path` escape-hatch), `wrap` (materialize-only, no Composer spawn; requires `source_ref`, forbids `packet_path`). `consult` is dropped — migrate to `light-bounded`. |
| `dispatch_thread_id` | `str` | Compaction key for server-owned thread persistence (`thread:dispatch:{id}`). Stable per arc/session. Context for non-packet dispatches is read from this thread's latest turn body. For `role=cursor-sdk` generate with `packet_path`, the packet is the instruction channel (bus turn ignored when both are present). Unused by `op="handoff"`. |
| `thread` | `str\|None` | Required when `op="to_thread"` — agent-bus thread ID |
| `subject` | `str\|None` | Bus reply subject (`to_thread`); required packet subject (`handoff`) |
| `model` | `str\|None` | Optional override; must be in persona's allowed set. Unused by `op="handoff"`. |
| `system` | `str\|None` | Extra caller-supplied system text appended during persona assembly |
| `reasoning_effort` | `str\|None` | Provider-native reasoning effort. Accepted values: `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`. Provider support varies (see `docs/thirdparty/{provider}/upstream` for the documented surface — e.g. OpenAI accepts `none/low/medium/high/xhigh`; Anthropic adaptive accepts `low/medium/high/xhigh/max`; Gemini 3 accepts `minimal/low/medium/high`). reasoning_effort is a portable intent label, not cross-provider semantic parity: the same value resolves to different provider-native shapes per value_kind. Unsupported-at-model values raise a ProtocolError (G9 reject-loudly) before dispatch — they are never silently dropped. Inspect the actual native resolution via the dispatch-envelope knob_resolution / member_knob_resolution echo (or resolve_dispatch()). |
| `caller_agent` | `str\|None` | Dispatch provenance |
| `timeout_seconds` | `int\|None` | Pipeline wall-clock cap |
| `source_ref` | `str\|None` | Admission ref (`todo:{slug}`, `plan:{slug}`, `plan_phase:{slug}[/phase-N]`, `agent-bus:N#turn-N`, `packet:{path}`). On `op="generate"` with `role=cursor-sdk`: drives `contract=implement` and `contract=wrap` — Stargate resolves `normalize → materialize → validate_packet` server-side from the source entity's **attributes** (`files_expected`, `acceptance_criteria`, `required_skills`, gate keys); the `source_uri` spec body is fingerprinted via `content_hash`, never content-read. On `op="handoff"`: same normalize/materialize path; **preferred for the implement lane** (`cursor-implement` / `web-implement`). `agent-bus:N` is gated unless an explicit `#turn-N` resolves it; `task:`/`project:` are grammar-excluded (containers, not dispatchable). Relay pass-through — the MCP client does NOT resolve it. |
| `packet_path` | `str\|None` | `op="generate"` with `role=cursor-sdk` — workspaces-relative path to instruction packet. Honored for `contract=light-bounded`, `pure-mechanical`, and `implement` (legacy hand-authored escape-hatch; implement also runs implement-ready gate). Default implement path is `source_ref`, not `packet_path`. When no `packet_path`, context comes from `dispatch_thread_id`'s latest turn (non-implement contracts) or server materialization (implement + `source_ref`). `op="handoff"` — hand-authored alternative to `source_ref`; both-present triggers `implement_spec_hash` drift guard. |
| `pointer_body` | `str\|None` | `op="handoff"` only — override the pointer turn body (≤25 lines) |
| `tags` | `list[str]\|None` | `op="handoff"` only — bus thread tags (default: `["agent:{to_agent}", "type:handoff", "contract:{handoff_contract}"]`). Caller-supplied tags are preserved; `contract:{value}` is appended if absent |
| `role=cursor-sdk` (op=generate) | — | **Default transport for bound mechanical implement.** SDK auto substrate; default delivery=thread; general-execution via `contract=light-bounded|pure-mechanical` with context on `dispatch_thread_id` or `packet_path` (packet wins when both present); implement via `source_ref=todo:{slug}` + `contract=implement` (server materialization + implement-ready gate; legacy `packet_path` escape-hatch); materialize-only via `contract=wrap` + `source_ref`; poll via `poll_hint` (agent-bus), not `pipeline(op=result)`. **Dense attributes required** for implement (Composer executes mechanically). `cursor-implement` handoff = operator-attended fallback |
| `op=handoff, seat=cursor-sdk` | — | **Deprecated** — normalizes to generate + warning (`deprecated_alias` in response) |

**`op="generate"` / `op="to_thread"` — admission guard for web/manual seats:**

Roles or seat slugs that resolve to a manual-handoff profile (`manual_handoff=true`,
e.g. `claude/web`, `grok/web`) are rejected **before** dispatch with 422
`web_seat_not_generate_target` — including when `model=` is supplied explicitly.
Valid generate roles: API-default roster slots (`reviewer`, `gatherer`,
`synthesizer`, `artisan`, `skeptic`). Invalid: seat slugs (`claude-web`, `web`),
web-default roles (`web-consult`, `web-implement`), and Cursor handoff-only roles
(`cursor-consult`, `cursor-implement`). Web Claude doing local file work should
use `fs` directly; peer consult → `team_dispatch(op=generate, role=…)` with optional `model=`.

**Future (`claude/web-auto`, Track 1 proposal — P0 gate green):** an unattended
`op=generate` path for `web-consult` / `web-implement` via a dedicated MCP worker + service
token is proposed (deck `tmp/prompts/web-generate-substrate/`). The P0 gate is proved —
`scripts/web-automation-substrate-smoke.py` validates headless vortex MCP auth + bus closeout
(env: `WEB_AUTOMATION_MCP_TOKEN`, `VORTEX_MCP_URL`; exit 0 → P1 densify unblocked, thread 1600).
P1+ is **not landed**: continue using the manual `claude/web` handoff (`web-consult` /
`web-implement` + operator push) until the `claude/web-auto` profile ships.

**`op="handoff"` — manual-seat handoff primitive** (dispatching agent → web or Cursor IDE):

Pick a **functional role**; seat and contract resolve together. Manual seats admit
only handoff on `team_dispatch` (not `generate`).

| Intent | Role | Seat | Operator |
|--------|------|------|----------|
| Web consult / dialectic | `web-consult` | `claude-web` | push bus message |
| Cursor consult / architecture | `cursor-consult` | `claude-cursor` | open IDE thread |
| **Bound implement** (→ Cursor) | `cursor-implement` | `claude-cursor` | open IDE thread |
| **Bound implement** (→ Web) | `web-implement` | `claude-web` | push bus message |

**Bound implement has two seats** (contract derived from the role slug; `handoff_contract=implement`
in the response): `role=cursor-implement` → `claude-cursor` (open IDE thread), and
`role=web-implement` → `claude-web` (operator push). Both require acceptance criteria in
`<task_guidance>`; the implement guardrails (acceptance-criteria lint, implement pointer line,
`contract:implement` tag) key on the derived contract + seat, not on a role name. Distinct from
the `*-consult` reasoning roles (which derive `consult` — they cannot raise the implement
guardrails). `model` and `handoff_contract` are not accepted on the handoff request — pick the
slug whose `{platform}-{contract}` encodes your intent. Web-native bound work without a
fresh-thread handoff: `Pick up todo:{slug}` (loads `implement-todo` skill).

See `projects/.cursor/rules/handoff-dispatchers.mdc` (§ web-claude for `role=web-consult`, §
`cursor-claude` for `role=cursor-consult`); consult index `agent-skills/consult-routing.md`.

Creates an agent-bus thread (e.g. `web-consult` / `web-implement` → `claude-web`,
`cursor-consult` / `cursor-implement` → `claude-cursor`)
and returns `{thread_id, subject, to_agent, resolved_handoff_seat, handoff_contract,
handoff_contract_source, push_reminder, result_handle, handoff_status,
poll_hint}` synchronously — no model is dispatched and no `execution_id` is minted.
(`resolved_handoff_seat` aliases `to_agent`; `handoff_contract_source` is always
`"role_default"`.)
`result_handle.kind` is `"agent_bus_thread"` (authoritative for retrieval — use
`agent_bus`, not `pipeline(op="result")`). Initial `handoff_status` is
`awaiting_first_reply`. `poll_hint` carries `tool` (`"wait"`), `arguments` (object,
human-readable), and `arguments_json` (string — **use this** for MCP `agent_bus`
calls; see `agent-skills/dispatch-shape.md`). Re-call with `wait_seconds` until
`status` is `complete`. Web seats start after the operator pushes the bus
message; Cursor seats start when the operator opens the thread in the IDE. The
endpoint enforces that the role resolves to a manual-handoff seat
(`delivery=manual, manual_handoff=true`); API-dispatchable roles (reviewer, gatherer, etc.)
are rejected with `handoff_requires_web_seat` 422.

**Self-handoff:** a manual seat may call `op="handoff"` with the matching roster
role (`claude-web` → `role=web-consult` or `role=web-implement`; `claude-cursor` → `role=cursor-consult` or `role=cursor-implement`) to open
a new agent-bus thread with packet-booted context. This is **supported** — distinct
from `op="generate"` to the same seat (422 `web_seat_not_generate_target`).
Authority: `projects/.cursor/rules/handoff-dispatchers.mdc` § Self-handoff;
`agent-skills/consult-routing.md`.

The pointer body defaults to the standard ≤25-line pointer template (see
`projects/.cursor/rules/handoff-dispatchers.mdc` and the durable packet skeleton
`docs/agent-guides/skills/handoff-packet-authoring.md`)
(packet path + six-block enumeration + reply instruction). Caller may supply
`pointer_body` override up to 25 lines. Longer overrides are rejected 422.

Caller **must** write the packet file before calling handoff; only a pointer is posted
to the bus. `push_reminder` in the response carries the formatted push instruction
when the handoff thread stays open and web must act (see `agent-bus-push-reminder_ws.mdc`).

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

**Handoff response fields** (beyond the core `{thread_id, subject, to_agent, …}` set):

| Field | When present | Meaning |
|---|---|---|
| `materialization_mode` | `source_ref` handoffs | `auto` (server materialized packet from source entity), `hand_authored` (caller `packet_path` only), or `hand_authored_traced` (both `source_ref` and `packet_path` with hash guard). |
| `implement_spec_hash` | Bound implement / traced handoffs | Server-stamped hash of the normalized implement spec (`sha256:…`). |
| `materialization_present` | `source_ref` materialize lane only, on miss | `false` when the materialized packet path is absent at the executor workspaces root (G-b probe). Omitted when probe passes. Handoff still admits (200) — graded warn, not 422. |
| `warnings` | Non-empty admission warnings | Includes `materialization.executor_absent: …` on G-b probe miss. Merged with packet validation warnings. |

Cross-mount probe activation: set Stargate env `HANDOFF_EXECUTOR_WORKSPACES_ROOT` to the
executor's workspaces mount when it differs from Stargate's write root. Unset (default) probes
the write root — zero regression on shared-mount deployments.

**Anti-patterns** (handoff):

- Calling `pipeline(op="result", execution_id=...)` — handoff returns no `execution_id`.
- Polling the agent-bus "most recent thread" instead of the returned `thread_id`.
- Client-side MCP poll loops or Stargate wait proxies — use one `agent_bus(wait)` per check.
- Treating `model=` on `team_dispatch` as spawning a web session (web seats reject generate).

Examples:

```python
# Generate — pre-stage context on dispatch_thread_id, then dispatch
team_dispatch(
    op="generate",
    role="gatherer",
    dispatch_thread_id="cursor-2026-06-02-design-review",
    contract="light-bounded",
    reasoning_effort="high",
    max_tool_turns=25,
    caller_agent="cursor",
)

# Bus mode — agent posts reply to thread 123
team_dispatch(
    op="to_thread",
    role="gatherer",
    dispatch_thread_id="cursor-2026-06-02-design-review",
    contract="light-bounded",
    thread="123",
    subject="Design review",
    reasoning_effort="high",
    max_tool_turns=25,
    caller_agent="cursor",
)

# Handoff mode — fresh-WEB dispatch to claude-web; operator push required
team_dispatch(op="handoff", role="web-consult",
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
team_dispatch(op="handoff", seat="claude-cursor",
              packet_path="universal-llm-gateway/tmp/reviews/<task>-cursor-packet.md",
              subject="<Task> handoff — <subject>")
# → {to_agent: "claude-cursor", push_reminder mentions Cursor / agent-bus}

# Bound implement (→ Cursor) — open IDE thread; PREFER source_ref (Stargate materializes the packet)
team_dispatch(op="handoff", seat="claude-cursor", contract="implement",
              source_ref="todo:<slug>",   # primary; Stargate normalize→materialize from the todo spec
              subject="Implement <task>")
# → {to_agent: "claude-cursor", handoff_contract: "implement", push_reminder mentions Cursor}
# (Legacy hand-authored alternative: packet_path="…/<task>-implement-packet.md")

# Bound implement (→ Cursor SDK) — DEFAULT; auto Composer, no IDE pickup
team_dispatch(op="generate", role="cursor-sdk", contract="implement",
              source_ref="todo:<slug>", dispatch_thread_id="<arc-id>")
# → poll agent_bus(wait) from poll_hint; server materializes from todo attributes
# (Legacy hand-authored alternative: packet_path="tmp/reviews/<task>-implement-packet.md")

# Bound implement (→ Web) — operator push; claude-web implements via fs
team_dispatch(op="handoff", role="web-implement",
              source_ref="todo:<slug>",
              subject="Implement <task>")
# → {to_agent: "claude-web", handoff_contract: "implement", push_reminder mentions web push}
```

Chat-Completions-only OpenAI search models (`openai/*-search-api`) are rejected
on dispatch. Use `llm_generate` for those.

**Provider-specific consult without handoff** — pick an API role and optional
`model=` override (must be in the role's `allowed_models`):

```python
# GPT review (default reviewer model) — pre-stage context on dispatch_thread_id
team_dispatch(op="generate", role="reviewer", dispatch_thread_id="arc-123",
              contract="light-bounded")

# Grok consult
team_dispatch(op="generate", role="artisan", model="xai/grok-4.3",
              dispatch_thread_id="arc-123",
              contract="light-bounded")
```

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
| `messages` | `list[dict]` | Latest user turn(s) per member — same compaction contract as `team_dispatch` |
| `dispatch_thread_id` | `str` | Server-owned compaction key (required) |
| `disposition` | `"panel"` | Must be `panel` |
| `include_synthesizer` | `bool` | Optional gemini tiebreaker |
| `poll` | `bool` | Block-poll each `execution_id` when true |
| `wait_seconds` | `int` | Per-member poll wait when `poll=true` (capped at 60) |
| `caller_agent` | `str\|None` | Dispatch provenance (forwarded to each member) |
| `system` | `str\|None` | Extra caller-supplied system text for all members |
| `reasoning_effort` | `str\|None` | Requested reasoning knob; actual resolution is reported in `member_knob_resolution`. No parity claim by default. |
| `generation_options` | `dict\|None` | Uniform provider pass-through; use only cross-provider-safe keys |
| `max_tool_turns` | `int\|None` | Tool-loop cap per member |
| `transcript_id` | `str\|None` | Provenance-only session id per member (not forwarded to models) |
| `timeout_seconds` | `int\|None` | Pipeline wall-clock cap per member |
| `source_ref` | `str\|None` | Workspaces-relative packet path or URI; read at admission and inlined into the first user message |
| `panel_request_id` | `str\|None` | Opt-in idempotency key; same id + equivalent inputs within dedupe window returns prior envelope without a second paid fan-out |

**Idempotency (`panel_request_id`):**
- Optional opt-in key. A repeat call with the same `panel_request_id` and equivalent inputs within the dedupe window (default 10 min) returns the prior dispatch envelope (`idempotency_hit: true`) without a second paid member fan-out. Omit it to force a fresh dispatch — the panel is stochastic, so intentional reruns require a new/rotated id (or no id).
- Equivalent inputs = `messages`, `dispatch_thread_id`, `disposition`, `include_synthesizer`, `system`, `source_ref`, `reasoning_effort`, `generation_options`, `max_tool_turns`, `timeout_seconds`. `poll`, `wait_seconds`, `caller_agent`, `transcript_id` do **not** affect dedupe identity.
- Reusing a `panel_request_id` with **changed** inputs returns `validation_error` (`idempotency_conflict`).
- On a hit with `poll=true`, the stored execution_ids are polled fresh (re-attach), so a client that lost the original response can recover live status without re-paying.
- Usage: have your client wrapper set a stable `panel_request_id` (e.g. derived from `dispatch_thread_id` + a task hash) so its own retries dedupe.

**Returns:** `submission_plan` (role, model, execution_id, dispatch_key per member);
reasoning transparency as `member_knob_resolution` per panel member;
`status` ∈ `{dispatched, partial, complete, failed}` — `dispatched` when `poll=false`
(members admitted, caller polls via `pipeline(op=result, ...)`); when `poll=true`:
`partial` if any member still running (includes `do_not_resubmit: true` and
`in_flight_execution_ids`), `complete` when all members succeeded, `failed` when
all terminal but ≥1 member failed — never bare pipeline `running`;
`member_status` maps each role to `{complete, running, failed}`;
aggregate `tokens_in` / `tokens_out` summed across polled members;
inspect `member_knob_resolution[*].status`, `parity`, and `notes`.

**Wire shape:** each member relay is a `team_dispatch(op=generate)` body. `model` is
omitted for role-default members (`skeptic`, `synthesizer`); `reviewer` carries an
explicit override. Auditability is via the returned `member_models` / `panel_families`
envelope and Menu D assert stamp — not wire-pinned models.

**Capability envelope:** `panel_capabilities` maps each member role to effective
`inline_only`, `mcp_enabled`, `tool_surface`, and `resolved_model` (mixed tiers:
skeptic inline-only, reviewer MCP-capable). Each member `dispatches` entry may also
carry `capabilities` from team_dispatch admission.

**Generate-only by design:** no `op=to_thread` or `op=handoff` fan-out — member outputs
must be polled and lead-adjudicated (Guard 2) before any bus delivery or conclusion.

**Timeout stack (4 layers — team-dispatch pipeline):**

| Layer | Knob | Default | Scope |
|-------|------|---------|-------|
| pipeline overall | `options.timeout_seconds` (= body `timeout_seconds` pass-through) | 14400 | whole pipeline |
| SSE overall (remote MCP) | `context.options.timeout_seconds`; fallback `REMOTE_MCP_OVERALL_TIMEOUT_S` | 300 | one provider SSE call |
| respond step | `steps.respond.timeout_seconds` | **1200** | the `respond` step |
| concurrency lock | `concurrency.timeout_seconds` on `dispatch:{dispatch_thread_id}` | **1200** | lock wait per member key |

Panel defaults: per-member `dispatch_thread_id` suffix (`{base}:{role}`) for true parallelism;
failure detection is event-based (observability), not timeout fast-fail.

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

On success:

```
{"status": "ok", "pipeline": "rag-context", "content_length": <int>,
 "duration_s": <float>, "context": "<assembled context with source labels>",
 "retrieval": {
   "resolved_scope": "...",
   "scope_confidence": 1.0,
   "chunks_found": <int>,
   "scope_rejected": false,
   "scope_source": "default_scope" | "classifier" | "user_override" | "prefix_override",
   "auto_classified": false,
   "scope_key": "...",
   ...
 }}
```

- **`retrieval`**: compact scope metadata from the rag-context retrieve step.
  `auto_classified` is `true` only when `scope_source=classifier` (LLM scope
  prediction ran). The MCP primary path uses the direct pipeline
  (`scope_source=default_scope` for unscoped calls).
- **`scope_note`**: present on unscoped success/empty paths when
  `scope_source` is `default_scope` or `classifier` (static advisory).

On error: `{"error": "<message>"}` plus optional `scope_note` and `retrieval`
when the pipeline completed with metadata before failing content assembly.

### Direct pipeline callers

When calling Stargate `/v1/chat/completions` with `model=rag-context`, pass
`pipeline_options.include_retrieval_metadata: true` to receive the same fields
under top-level `pipeline.retrieval` (MCP `rag_search` maps this to
top-level `retrieval`).

## rag_answer

**DEBUG ONLY** — buried MCP surface for exercising the `rag-answer` /
`rag-answer-deep` pipelines. Agents must use `rag_search` for retrieval work.

Delegates retrieval to `rag-context` via an internal `pipeline_call_v1` step,
then runs relevance gating and answer generation.

### Args

| Arg | Type | Description |
|---|---|---|
| `question` | str | Natural language question — REQUIRED |
| `scope` | str\|list\|None | Same semantics as `rag_search` |
| `prefix` | str\|list\|None | Source path prefix; mutually exclusive with `scope` |
| `deep` | bool | Use `rag-answer-deep` iterative retrieval (default false) |

### Returns

On success: same `retrieval` + optional `scope_note` shape as `rag_search`,
with grounded text in `answer` instead of raw `context`:

```
{"status": "ok", "pipeline": "rag-answer", "content_length": <int>,
 "duration_s": <float>, "answer": "<grounded answer>", "retrieval": {...}}
```

## cortex

Cortex knowledge system — entities, assertions, relationships, edges, journals.

### Operations

| Op | Args | Description |
|---|---|---|
| `entities` | type?, limit? | List entities |
| `entity_get` | entity_id, include_edges?, edge_limit? | Get entity with assertions + relationships. Pass `include_edges=true` to also return `reasoning_edges` (active session edges). `edge_limit` defaults to 20. |
| `entity_create` | id, type, name, description?, workflow_state?, notes?, aliases?, attributes?, source_uri?, content_hash? | Create entity (409 if exists). Option-C traits (`confidence_band`, `lifecycle`, `adoption`) are derived at birth — set via `entity_update` after create (422 if passed on create). |
| `entity_update` | entity_id, name?, description?, workflow_state?, confidence_band?, lifecycle?, adoption?, notes?, aliases?, attributes?, source_uri?, content_hash? | Update mutable fields. Trait write surface: `confidence_band`, `lifecycle`, `adoption`. null clears; omit leaves untouched. |
| `assertions` | entity_id?, entity_id_prefix?, filter?, seeded_by?, confidence?, review_status?, superseded?, limit? | List assertions. `seeded_by` filters stored provenance (post-projection family or passthrough pipeline id). review_status: committed/flagged/staged/rejected |
| `assert` | entity_id, claim, confidence, evidence, evidence_uris?, seeded_by?, derivation_type?, confidence_score?, observed_at?, valid_from?, chunk_id? | Direct write. `seeded_by` write: server projects seat→family; bare family unchanged; pipeline/unrecognized pass through (never rejected). Response adds `seeded_by_input`, `seeded_by`, `seeded_by_projection` (`seat_to_family`\|`identity`\|`passthrough_unrecognized`). confidence: confirmed/believed/suspected/hypothesized. derivation_type: quotation/compression/inference/other |
| `assertion_update` | assertion_id, superseded_by?, valid_until?, confidence?, confidence_score?, review_status?, reviewer?, reviewed_at? | Update assertion metadata |
| `supersede` | old_assertion_id, entity_id, claim, confidence, evidence, session_id, agent, evidence_uris?, valid_from?, derivation_type?, seeded_by? | Atomic close-old + create-new. Optional `seeded_by` uses the same projection as `assert`. Also auto-creates the `supersedes` edge in the same transaction. |
| `relationships` | entity_id?, type_id?, limit? | List with names, strength |
| `relationship_create` | source_id, target_id, type_id, role?, strength?, evidence?, chunk_id?, valid_from?, valid_until?, source_uri?, session_id?, agent? | Create structural relationship. Pass `session_id` + `agent` for provenance (nullable; recommended for new writes). |
| `stats` | — | Dashboard counts |
| `search` | query, limit?, superseded?, entity_type?, intent? | FTS5 hybrid search over assertions. `intent`=`summary` (default): compact hits; `intent`=`full`: detail rows with enrichment fields. Prefer over `assertions` for natural-language queries |
| `impact` | entity_id, depth? | Transitive reverse-dependency BFS from entity. Returns `{seed_entity, depth, impacted_entities: [{entity_id, entity_name, hop_distance, path_trace, assertion_count, edge_types, substrates}], total_impacted_assertions}`. Walks both substrates via `_DEPENDENCY_EDGE_TYPES` (`requires`, `depends_on`, `derived_from`, `evidence_for`, `extends`). |
| `activate` | entity_ids, depth?, max_results?, exclude_ids?, suppress_hubs?, decay_factor? | Spreading activation from seed entities. Returns `{seed_entities, depth, hub_suppression, count, activated: [{assertion_id, entity_id, claim, confidence, entrenchment_score, activation_score, hop_distance, activation_path, edge_types_traversed, substrates_traversed}]}`. Walks both substrates via the full 15-type association set. `entity_ids` is comma-separated. |
| `journal_read` | limit?, agent? | Recent session journals; `agent` filters by seat-level operational identity |
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
| `signal-events` | signal? | Recent events for a signal pattern (`signal` supports `*` glob with literal `_`, `%`/`_` raw SQL LIKE, or exact match) |
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

Both `sandbox` and `op` are REQUIRED — there is no default or path-based inference.
Omitting `sandbox` returns a structured recoverable error naming both roots:
`cortex` (/data/files — notes, agent-skills, threads, dropbox) and
`workspaces` (repo source under `/mnt/torus/projects`, paths must include the
repo prefix). Path shapes such as `notes/…`, `tasks/…`, `tmp/…`,
`agent-skills/…`, and `services/…` exist under **both** stores; disambiguate
explicitly. Repo-prefixed paths (e.g. `universal-llm-gateway/…`) are advisory
workspaces hints only — you must still pass `sandbox`.

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
| `run` | `pipeline_id`, `messages` | `{content, model, duration_s, execution_id?, usage?, pipeline?}` — blocks until pipeline completes; `pipeline` mirrors Stargate when present (e.g. `retrieval` when `options.include_retrieval_metadata=true`) |
| `async` | `pipeline_id`, `messages` | `{execution_id, pipeline, started_at, status}` — fire-and-forget; use for calls expected to exceed the MCP 300s read-timeout ceiling |
| `result` | `execution_id` | Tracker record: `{execution_id, pipeline, status, started_at, completed_at, result, error}` |
| `validate` | `pipeline_id` | `{valid, pipeline, steps, models, domain, errors}` — no inference compute consumed |

### Examples

```
pipeline(op="validate", pipeline_id="gatherer-dispatch")
pipeline(op="run", pipeline_id="rag-context",
         messages=[{"role":"user","content":"..."}],
         options={"include_retrieval_metadata": true})
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

Unified boot briefing for session start. Seat-scoped: each `{family}-{platform}` slug gets tailored content.

### Args

All parameters optional. **Default seat when nothing is passed:** `family=claude`, `platform=cursor` → **`claude-cursor`**.

| Arg | Default | Description |
|---|---|---|
| `agent` | — | Primary seat slug: `claude-web`, `web` (→ `claude-web`), `claude-cursor`, `cursor` (→ `claude-cursor`), … Overrides `family`/`platform` when slug parses as `{family}-{platform}`. |
| `family` | `claude` | Model family: `claude`, `gpt`, `grok`, `gemini` |
| `platform` | `cursor` | Surface: `cursor`, `web`, `api` |
| `role` | — | Functional annotation: `lead`, `reviewer`, `gatherer`, … Does **not** change seat slug or default to `lead`. |
| `transcript_id` | `""` | Continuation from a **closed** session transcript entity |
| `views` | — | Entity ids for subgraph manifest entries on the card |
| `principal` | — | Principal entity id (e.g. `person:…`) for head block |
| `profile` | — | `"dispatch"` for dispatch-scoped inject |
| `packet_text` | — | Packet text for `<invariants>` skill parse when `profile="dispatch"` |

**Web lead:** `cortex_boot(agent="claude-web", role="lead")`.

Bound ULG coding sessions may **skip** boot when task + skill preload suffice — see
`docs/agent-guides/skills/web-boot-lead.md`.

### Response fields (key selection)

| Field | Description |
|---|---|
| `session_id` | Server-minted ID `{family}-{platform}-YYYY-MM-DD-HHMMSS-{3hex}` UTC. Hold for asserts, edges, `session_close`. |
| `briefing_card` | Compact Markdown briefing (~3–8KB target): deadlines, bus, todos, skills index, … |
| `sections_available` | Manifest of deeper-pull sections with fetch hints |
| `operational_context_ref` | Path to operational context file (read on demand via `fs md_read`) |
| `seat_preloaded` | Slugs merged into `skill_suggest` loaded set (web orientation/inject channels) |
| `injected_artifacts` | Byte ledger: `name`, `mode`, `source`, `bytes`, `sha256`, `path`, `fetches` |
| `audit_dump_path` | Per-boot audit sidecar (`{agent}-YYYY-MM-DD-HHMMSS.md`); filename decoupled from `session_id` (which adds a 3-hex suffix for uniqueness); LIVE mode only; `null` on failure |

Legacy field names in older notes (`boot_narrative`, `agent_bus` as top-level keys) may appear in
audit dumps; MCP response uses `briefing_card` + `sections_available`.

## boot_inspect

Read-only inspection surface for boot payload auditing and profile diffs.

`boot_inspect` runs the same fetch/render graph as `cortex_boot`, but in inspect
mode (`mode="inspect"`): no operational-context file write, no audit dump file
write, and no `mcp.cortex.boot*` event emission.

### Args

| Arg | Default | Description |
|---|---|---|
| `agent` | — | Seat slug (same semantics as `cortex_boot`); use with `family`/`platform` when omitted |
| `family` | `claude` | Primary family when `agent` absent |
| `platform` | `cursor` | Primary platform when `agent` absent |
| `transcript_id` | `""` | Optional continuation transcript for primary profile |
| `diff_with` | `""` | Optional secondary seat slug (`claude-web`, …) → returns `primary`, `secondary`, `diff` |

### Response fields (key selection)

| Field | Description |
|---|---|
| `mode` | Always `inspect` |
| `operational_context_inline` | Full rendered operational context text (inline, never written in inspect mode) |
| `operational_context_ref` | Always `null` in inspect mode |
| `audit_dump_path` | Always `null` in inspect mode |
| `injected_artifacts` | Same manifest schema as `cortex_boot`; `operational_context` artifact is `mode: inline` in inspect mode |
| `diff` | Present only when `diff_with` is set. Contains `artifacts_only_in_primary`, `artifacts_only_in_secondary`, and `artifacts_with_delta` (`kind: inline_canonical_text` or `sha256_mismatch`) |

## skill_suggest

In-session skill delta for web/API seats. Ranked slugs **not** already in `loaded[]` ∪ `seat_preloaded`.

### Args

| Arg | Required | Description |
|---|---|---|
| `loaded` | **yes** | Slugs fetched this session (maintain list across calls). Server merges `seat_preloaded` for web. |
| `conversation_context` | no | Task read (≤16k chars). Omit → `insufficient_context`. |
| `limit` | no | Max suggestions (default 8) |
| `agent` | no | Seat slug when session resolution fails |

See `docs/agent-guides/skills/skill-suggest-utilization.md` and `web-boot-lead.md`.

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

Run ruff lint, compileall, import checks, and (conditionally) Lane A offline pytest. Use after
modifying code.

### Args

- `files`: list of file paths relative to project root

### Returns

`{"passed": true/false, "ruff": {...}, "compile": {...}, "imports": {...}, "tests": {...}}`

When any path in `files` touches `libs/llm_adapters/` or `libs/model_id/`, `tests` runs
`pytest -m offline -q` over those trees. Otherwise `tests.passed` is true with output
`"no offline-closure files touched; skipped"`.

Outside Lane A, pytest is not invokable via this tool — use it for lint/compile/import closure;
`claude-web` (lead seat) closes liveness via `manage` without a Cursor handoff for verify-only.

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

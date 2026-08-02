# /modularize — Frontier End-to-End

Modularize a file or set of files to comply with SLOC and SRP guidelines.

the frontier dispatcher (`team-generate`, gpt-5.5) owns **both** plan generation (Phase 1) and
execution (Phase 2). Cursor's role is packet construction, audit, the user
approval gate, and final verification only. Same agent across both phases means
the frontier dispatcher can verify its own Phase 1 reasoning against the live filesystem during
Phase 2 implementation — self-consistency that a separate executor cannot give.

**Relation to architecture-handoff-protocol**: /modularize is a task adapter
under `architecture-handoff-protocol.mdc`. Packet skeleton:
`universal-llm-gateway/tmp/reviews/_handoff-packet-template.md`; transport axis:
`agent-skills/consult-routing.md`. It uses the six-block packet shape,
the rule-blind invariants composition, and iteration via `<prior_pass>`. It
deliberately replaces the protocol's **five-bucket validation contract** with
a **structural audit** (§2.6) because modularization produces new code rather
than evaluative findings — there is nothing to "reject by rule" or "surface for
triage". The protocol's **documentation contract audit** is preserved and runs
in §3.5.

## Three Flows

| Flow | Trigger | Path |
|---|---|---|
| **A — Full** | source file argument (e.g. `libs/foo/bar.py`) | Cursor builds packet → frontier Phase 1 → user approval → frontier Phase 2 → Cursor verifies |
| **B — Implement only** | plan file in `tmp/modularize-plans/{name}.md` | Skip to frontier Phase 2 → Cursor verifies |
| **C — Resume** | state file `tmp/modularize-plans/{name}-state.md` | Re-dispatch frontier Phase 2 with `<prior_pass>` block → Cursor verifies |

**Flow detection rule** (apply in order):
1. Path basename ends with `-state.md` → Flow C
2. Path is in `tmp/modularize-plans/` and ends with `.md` → Flow B
3. Otherwise → Flow A

All three flows require Stargate on `:9999`. If Stargate is unavailable: stop
and surface the error. ¬ fall back to local execution — frontier dispatch is
mandatory.

**Resume tradeoff**: The state-file resume path covers single-interruption
recovery within a session. For jobs that need true multi-session history
(very large files split across days, repeated interruptions, or interactive
multi-turn correction), use the `web-claude` dispatcher instead — it has
persistent agent_bus thread state across sessions.

## When to Use

- A file you're editing exceeds 300 SLOC or has grown during this session
- After `scripts/modularize scan` reveals red/yellow files
- When the consultation-workflow rule flags a modularization need
- You want full reasoning-to-implementation accountability under one agent
  (preferred for files >400 SLOC or with complex consumer graphs)
- You have a pre-generated plan or state file from a previous run

**Note on `scripts/modularize plan`**: The legacy pipeline
(`scripts/modularize plan {file}`) is now an **optional cheaper alternative**
when Stargate load is high or you only need a quick structural sketch. It uses
`gpt-5.5` (plan model) + `gpt-5.4` (execute model) but lacks the frontier dispatcher's live-read
context and the plan→execute self-consistency. Prefer the frontier E2E path for
any file >400 SLOC or with complex consumer graphs.

## Instructions

### 1. Identify Target (Flow A only)

If the user specified a source file, use it. Otherwise:

```bash
source ~/.venvs/universal/bin/activate
python scripts/modularize scan {path}
```

Read `tmp/modularize-scan.md`. Present red and yellow files. Ask which to
refactor (or accept "all red").

### 2. Cursor Builds Packet + Frontier Phase 1 dispatch (Flow A only)

Cursor does **not** call `scripts/modularize plan`. Cursor builds the packet
directly and dispatches to frontier. Sequence:

#### 2.1 Gather artifacts

1. Read the full source file via `Read`.
2. Discover consumers:
   ```bash
   rg "from {module_dotted_path}|import {module_dotted_path}" --type py
   ```
3. Capture current git SHA for traceability:
   ```bash
   git rev-parse HEAD
   ```

#### 2.2 Compose `<invariants>` block

Per `architecture-handoff-protocol.mdc` § "Block 2", build a composed block —
do **not** just paste the cache file verbatim. Include:

1. **Architecture skill delivery (BINDING for CDP / web-anthropic):**
   **Inline** into `<invariants>` (these are **not** Claude slugs — ¬ slash):
   - `architecture-invariants` (universal floor)
   - `modularize-discipline` (split rules)
   - `ulg-architecture` `[ulg:*]` excerpt when splitting ULG `services/` / `libs/`

   Cortex stage of full skills is optional backup only — **¬** URI-cite as sole
   delivery. **¬** retired `cortex://agent-skills/*.md`. **Fail closed** without
   the inlines.

2. Full contents of `.cursor/invariants/modularize.md` (modularize-specific
   rules layered atop the universal invariants above)
3. Any loaded workspace rules relevant to the file's domain (e.g. add the MCP
   relay invariant if it's an MCP tool handler; add the REST-namespace rule if
   it defines `/v1/*` endpoints; add the universal-protocol rule if it touches
   transport)

End the block with the **critical instruction to the frontier dispatcher**:

> "The `<invariants>` block is the sole architectural authority for this task.
> ¬ consult project docs, README files, or architecture docs to fill in
> gaps — they may be outdated or incomplete. If you encounter a pattern in
> the source that conflicts with the invariants, the invariants win. Flag the
> conflict rather than replicating the source pattern."

#### 2.2b Compose `<architecture>` block

The `<architecture>` block is a concrete before→after replacement table. Where
`<invariants>` states *rules*, `<architecture>` states *canonical replacements*
so the frontier dispatcher can perform corrective refactoring mechanically without inferring them
from abstract rules.

Build the block by scanning the source file for each violation pattern. The
canonical table for this workspace:

| Violation pattern | Canonical replacement | Invariant source |
|---|---|---|
| `import logging` / `logging.getLogger(...)` | `from universal_logging import get_logger` / `logger = get_logger(__name__)` | `core_ws.mdc` |
| `httpx.AsyncClient(...)` directly | `from transport_utils import make_async_client` + `make_async_client(url)` | `uds-only-transport.mdc` |
| `httpx.Client(...)` directly | `from transport_utils import make_sync_client` + `make_sync_client(url)` | `uds-only-transport.mdc` |
| `os.getenv("..._SOCK", ...)` for socket paths | `from transport_utils import {SOCKET_PATH_CONSTANT}` | `uds-only-transport.mdc` |
| `Union[X, Y]` type hints | `X \| Y` (Python 3.12+) | `core_ws.mdc` quality gates |
| `Event(signal=..., payload=...)` direct construction | `@event_factory` decorated helper function | `event-factory.mdc` |
| `str(model_id)` used for identity/routing | `model_id.routing_key` or `model_id.normalized` per use | `modelid.mdc` |
| `from catalog import ...` / `from gateway_manager import ...` in handler bodies | HTTP call via `make_async_client` to the relevant REST endpoint | `rest-api-preference_ws.mdc` |

**Compose step**: read the source file top-to-bottom and note which patterns
from the table above are present. Write the `<architecture>` block as a
filtered version of this table — include only rows where the pattern actually
appears in the source. If none are present, write:

```
<architecture>
No canonical-replacement targets found in this source file.
All invariant violation patterns from the workspace table are absent.
</architecture>
```

This scoped table prevents the frontier dispatcher from proposing spurious replacements for
patterns not present in the file, while giving it unambiguous mechanical
targets for those that are.

#### 2.3 Write packet file

`tmp/modularize-plans/{sanitized_name}-packet.md` — six-block format from
`architecture-handoff-protocol.mdc` plus one modularize-specific block. Per
`projects/.cursor/rules/handoff-dispatchers.mdc` § team-generate, the packet is partitioned across
system + user. Keep the **full packet text in one file** (so Phase 2 can
re-read it) and split at dispatch time.

| Block | Belongs in | Contents |
|---|---|---|
| `<scope>` | user | source path, git SHA, SLOC count, consumer count, one-line topic |
| `<invariants>` | system | the composed block from 2.2 |
| `<architecture>` | system | canonical before→after replacement table for invariant violations found in this codebase (see 2.2b) |
| `<mcp_capabilities>` | system | `team-generate` template (the frontier dispatcher uses `fs` for reads/writes; `observability` for failure-mode verification) |
| `<output_format>` | system | "Reply with the modularization plan in MODULES + IMPLEMENTATION GUIDE + DEDUPLICATION OPPORTUNITIES sections, matching the legacy pipeline structure" |
| `<task_guidance>` (Phase 1) | user | "Read the source file at the path in `<scope>` live via `fs(sandbox='workspaces', op='read', ...)`. Read each consumer in `<corpus>` to verify which symbols are imported externally. Produce a complete modularization plan. Every proposed module name must pass the forbidden-name check in `<invariants>`. When the source contains any pattern listed in `<architecture>`, replace it with the canonical target — do not replicate the violation into new modules. Flag any other source patterns that conflict with `<invariants>` rather than replicating them." |
| `<corpus>` | user | Two subsections: `## Source` (full file contents) and `## Consumers` (list of `path: import_lines` for each consumer found via grep) |

#### 2.4 Dispatch Phase 1

```python
EXEC = team_dispatch(op="generate",
    role="reviewer",
    dispatch_thread_id=f"modularize-{TARGET_PATH}",
    messages=[{"role": "user", "content": <user-side blocks>}],
    model="openai/gpt-5.5",
    system=<system-side blocks>,
    reasoning_effort="high",
    caller_agent="cursor",
    max_tool_turns=15,
)
EXEC_ID = EXEC["execution_id"]
```

Save `EXEC_ID` for the state file later.

#### 2.5 Poll for result

```python
result = pipeline(op="result", execution_id=EXEC_ID, wait_seconds=60)
```

Re-poll up to 5 × 60s (5-minute ceiling). If still pending after the ceiling,
save `EXEC_ID` and surface to user — they can resume polling later.

| `result["status"]` | Reaction |
|---|---|
| `completed` | Extract `result["result"]["content"]` as the frontier plan |
| `failed` | Surface `result["result"]["error"]` verbatim. ¬ retry blindly |
| `pending`/`running` after ceiling | Save EXEC_ID; ask user to resume polling |

**Tool-loop exhaustion check**: if `status: completed` but `content == ""`,
the frontier dispatcher likely hit `max_tool_turns` mid-tool-loop. The dispatch envelope now
carries `finish_reason` directly — `finish_reason ∈ {"tool_calls", "tool_use",
"length"}` confirms a ceiling hit (Stargate also emits
`pipeline.frontier.dispatch.exhausted` with `enforcement` set to `client` or
`provider` in this case). The legacy `usage.completion_tokens > 0` heuristic
no longer fires reliably — provider-side ceilings can return small nonzero
token counts from tool accounting overhead (28 tokens observed on execution
`e07481c4`). Trust `finish_reason` instead.

Re-dispatch once with `max_tool_turns=25`. After a second exhaustion, surface
to user. Confirm via
`observability(operation="pipeline-trace", params={"execution_id": EXEC_ID})`.

#### 2.6 Cursor audits the frontier plan

Mandatory checks before presenting to user:

1. **Module names**: flag forbidden filenames (`utils.py`, `helpers.py`,
   `common.py`, `misc.py`, `base.py`)
2. **Cross-module imports**: no direct `httpx` (must use `transport_utils`),
   no raw socket construction
3. **Public surface**: `init_reexports` should not expose internal helpers
   (names beginning with `_`) unless explicitly required by a consumer
4. **Logger violations**: no proposed `logging.getLogger(...)` declarations —
   `universal_logging.get_logger` only

If any check fails: pushback via re-dispatch with a `<prior_pass>`
block listing the audit failures, OR surface to user for direction.

If audit passes:
- Write the frontier plan to `tmp/modularize-plans/{name}.md` (compatible with
  Flow B path)
- Present summary: deduplication opportunities, improvements, module count,
  audit results
- Ask: "Proceed with frontier executing this plan?"

### 2b. State File + frontier Phase 2 (Flows A, B, C)

After user approval (Flow A), or directly on entry (Flows B and C):

#### 2b.1 Create or update state file

`tmp/modularize-plans/{name}-state.md`:

```markdown
---
source: {full source path}
git_sha: {sha at start}
packet: tmp/modularize-plans/{name}-packet.md
plan: tmp/modularize-plans/{name}.md
phase: 2
last_completed_step: 0
files_pending:
  - {path1}
  - {path2}
files_written: []
execution_ids: []
errors: []
---
```

**Schema** (canonical):

| Field | Type | Meaning |
|---|---|---|
| `source` | string | Original source file path |
| `git_sha` | string | HEAD at start of session — for stale-resume detection |
| `packet` | string | Path to packet file |
| `plan` | string | Path to the frontier plan |
| `phase` | `1` \| `2` \| `done` | Lifecycle stage |
| `last_completed_step` | int | IMPLEMENTATION GUIDE step number last verified complete |
| `files_pending` | list[string] | Paths still needing to write |
| `files_written` | list[string] | Paths confirmed on disk |
| `execution_ids` | list[string] | All dispatch execution IDs (each redispatch appends) |
| `errors` | list[string] | Any failures encountered (transport, exhaustion, audit) |

#### 2b.2 Build Phase 2 packet

System block (re-use Phase 1 system: invariants + **architecture** + mcp_capabilities + output_format).

User block:
- `<scope>`: same as Phase 1 plus a one-line note "Phase 2 — execute the plan"
- `<task_guidance>` (Phase 2):

  > "You produced this plan in Phase 1. Execute it now using the IMPLEMENTATION
  > GUIDE as your mechanical roadmap. For each step:
  > 1. Read any source the step references via `fs(sandbox='workspaces', op='read', ...)`.
  > 2. Write the new file via `fs(sandbox='workspaces', op='write', path='{repo}/{path}', content=...)`.
  > 3. Read the file back via `fs` to verify the write landed and content is
  >    what you intended.
  > 4. Move on to the next step.
  >
  > When deleting the original source file, first read the package
  > `__init__.py` to confirm all required re-exports are in place. Only delete
  > if `__init__.py` is verified.
  >
  > Record every action and final SLOC counts in your output. End with a
  > 'Files written' summary listing every path you wrote."

- `<corpus>`: `## Plan` (the frontier plan from Phase 1) + `## State` (current state
  file contents — for Flow C this carries the `<prior_pass>` info)
- `<prior_pass>` (Flow C only): "On a previous pass, the following IMPLEMENTATION
  GUIDE steps were completed and verified on disk: {step numbers + paths}.
  Do not re-write those files. Resume from step {last_completed_step + 1}.
  Read each previously-written file once before proceeding to confirm contents
  match your plan."

#### 2b.3 Dispatch Phase 2

```python
EXEC = team_dispatch(op="generate",
    role="reviewer",
    dispatch_thread_id=f"modularize-{TARGET_PATH}-phase2",
    messages=[{"role": "user", "content": <user-side blocks>}],
    model="openai/gpt-5.5",
    system=<system-side blocks>,
    reasoning_effort="high",
    caller_agent="cursor",
    max_tool_turns=40,
)
EXEC_ID = EXEC["execution_id"]
```

Append `EXEC_ID` to state file's `execution_ids`.

**Turn budget sizing** (Phase 2 burns ~6 turns per new module: read source,
read consumers, write file, read-back verify, modify consumers, audit):

| New module count | `max_tool_turns` |
|---|---|
| ≤ 3 modules | `25` (default safe) |
| 4–6 modules | `40` (Phase 2 default) |
| 7+ modules / very large splits | `60` after first exhaustion |

`25` is only safe for ≤ 3 new modules. Confirmed exhaustion at 25 turns on a
302-SLOC 4-module split (execution `e07481c4`,
todo:frontier-dispatch-empty-content-exhaustion).

#### 2b.4 Poll Phase 2

Same as 2.5 with the same status/exhaustion/error handling. Phase 2 typically
needs longer polling — re-poll up to 10 × 60s (10-minute ceiling) for large
files, since `fs` writes incur per-file roundtrip.

#### 2b.5 Update state file

After the frontier dispatcher's response:

**Empty-content fallback** (ceiling-hit mid-loop): when `result["content"] == ""`
the "Files written" summary is missing, but earlier turns may already have
written most or all of `files_pending` to disk before the ceiling cut the
read-back-verify turn. Do NOT treat empty content as wholesale failure.
Instead, fall back to a direct disk audit:

1. For every path in `files_pending`, call
   `fs(sandbox='workspaces', op='read', path=...)`.
2. Paths that read back successfully move to `files_written` and are removed
   from `files_pending`.
3. If `files_pending` is empty after the disk audit: declare Phase 2 complete
   and proceed to Cursor verify (§3). The read-back-verify turn was the only
   thing the ceiling cut.
4. If `files_pending` is still non-empty: re-dispatch Phase 2 with Flow C
   (`<prior_pass>` listing the confirmed `files_written`) starting from
   `last_completed_step + 1`.

**Normal path** (non-empty content):
- Parse the "Files written" summary from `result["content"]`
- Cross-check each path with `fs(sandbox='workspaces', op='read', ...)` (or
  `Read` from Cursor) — only paths that exist on disk move to `files_written`
- Remove confirmed paths from `files_pending`
- Update `last_completed_step` to the highest IMPLEMENTATION GUIDE step number
  whose files are all in `files_written`
- If `files_pending` is non-empty: stop and surface to user. They can re-run
  the command pointing at the state file to resume.
- If `files_pending` is empty: set `phase: done`, proceed to Cursor verify.

### 3. Cursor Verifies

Once Phase 2 reports complete and `files_pending == []`:

```bash
# Compile every newly-created file plus every consumer
python -m compileall -q {newly-created package dir} {consumer paths}

# Lint check
ruff check {all files written by the frontier dispatcher} {consumer files modified by the frontier dispatcher}
```

If compileall fails:
- Read the failure to find the broken import
- Either fix manually (single-line typos) or re-dispatch frontier Phase 2 with a
  `<prior_pass>` block describing the failure (let the frontier dispatcher fix what it broke)

If ruff fails: re-dispatch to frontier with the lint output, OR run
`ruff check --fix && ruff format` if the failure is autofixable.

Update state file: `phase: done` once compile + lint both pass.

### 3.5 Documentation Contract Audit

Per `architecture-handoff-protocol.mdc` § "Documentation Contract Audit", check
whether the split changed any user-visible contract before closing:

| Check | Action |
|---|---|
| Did any new module emit events not present in source? | Audit `docs/event-contracts.md` and update if signals were added/changed |
| Did the split alter API surface (route paths, request/response shape, public function signatures)? | Update relevant API or integration docs |
| Did `__init__.py` re-exports change the public import surface? | Update relevant module README_AI.md or architecture doc |
| Pure structural split (no event/API/runtime change)? | Record "not needed: pure structural split" |

Record the audit result in the §4 report:

```
## Documentation Contract Audit
- `docs/event-contracts.md`: updated | not needed: <reason>
- other docs: <paths> | not needed: <reason>
```

Most modularizations are pure structural splits and the audit yields "not
needed" — but it must be performed and recorded, not skipped.

### 4. Report to User

- Files created (with final SLOC count of each)
- Files modified (consumers + new `__init__.py`)
- Original SLOC → sum of new module SLOCs
- Dispatcher: frontier E2E (`openai/gpt-5.5` × 2 phases)
- Tech debt addressed (legacy `logging.getLogger` calls replaced, etc.)
- State file path (kept for audit; remove only if user requests)

## Failure Handling

Per `projects/.cursor/rules/handoff-dispatchers.mdc` § team-generate "Failure handling":

| Failure | Reaction |
|---|---|
| Stargate transport error before dispatch | Stop. Surface error. Offer (1) start Stargate and rerun, (2) fall back to `web-claude` dispatcher (manual). ¬ local execution. |
| Tool-resolution error (`result.error` references unresolved tool) | Stop. Report `result.error` verbatim. Most likely cause: `MCP_PUBLIC_URL`/`MCP_AUTH_TOKEN` not configured on Stargate. |
| Tool-loop exhausted (Phase 1 or 2) | Re-dispatch once with `max_tool_turns=25` (Phase 1) or `max_tool_turns=60` (Phase 2 — bumped from the §2b.3 default of 40). Confirm via `pipeline-trace` (`pipeline.frontier.dispatch.exhausted` with `enforcement` ∈ {`client`, `provider`}). After second exhaustion, surface and stop. |
| `status: failed` | Report `result.error` verbatim. Offer fall-back to `web-claude`. |
| Phase 1 audit failure (forbidden filename, public-surface leak) | Re-dispatch to frontier with `<prior_pass>` listing the audit findings. ¬ apply blindly; ¬ silently rename. |
| Phase 2 partial completion | State file captures `files_written`. User can resume by re-running command with state file path (Flow C). |

## Rules

- ¬ execute Phase 2 without explicit user approval after Phase 1 audit
- ¬ skip the audit (rule-blind reviewer rule applies — the frontier dispatcher does not see
  `.mdc` rules, only the `<invariants>` block you compose)
- ¬ deviate from the frontier dispatcher's plan structure during Phase 2 — if the plan is
  wrong, re-dispatch Phase 1 with `<prior_pass>` rather than patching mid-flight
- ¬ skip the final compile + ruff check
- ¬ refactor files in `pipelines.local/` (frozen legacy)
- Output plans, packets, and state files all go to `tmp/modularize-plans/`
  (ephemeral, not committed)
- Every created module, class, and public function must have substantive
  docstrings (per `<invariants>`)
- `universal_logging` is mandatory for all new modules — replace any source
  `logging.getLogger(...)` with `from universal_logging import get_logger;
  logger = get_logger(__name__)`
- If Stargate is unavailable: stop and surface error. ¬ local fallback.

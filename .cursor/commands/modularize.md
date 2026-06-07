# /modularize

Modularize a file or set of files to comply with SLOC and SRP guidelines.

Two execution modes:

- **Default (frontier E2E)**: frontier (`frontier-mcp`, gpt-5.5) owns **both** Phase 1 (planning) and
  Phase 2 (execution). Cursor's role is packet construction, audit, the user
  approval gate, and final verification only. Same agent across both phases means
  frontier can verify its own Phase 1 reasoning against the live filesystem during
  Phase 2 implementation — self-consistency that a separate executor cannot give.
- **`--grok` mode**: frontier owns Phase 1 (planning); grok-build in a git worktree
  owns Phase 2 (execution). Preferred for large mechanical splits where Phase 2 is
  purely mechanical — saves frontier API cost by routing execution to grok-build.

**Relation to architecture-handoff-protocol**: /modularize is a task adapter
under `architecture-handoff-protocol.mdc`. It uses the six-block packet shape,
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

> **`--grok` flag**: applies to Phase 2 across all three flows. The flag changes
> the Phase 2 executor (frontier → grok-build in a git worktree), not the flow
> selection logic. Phase 1 planning always runs on frontier regardless of this flag.

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
- `--grok` flag: prefer for large files (>600 SLOC) or splits with >6 modules
  where Phase 2 is purely mechanical — saves frontier API cost by routing
  execution to grok-build

**Note on `scripts/modularize plan`**: The legacy pipeline
(`scripts/modularize plan {file}`) is now an **optional cheaper alternative**
when Stargate load is high or you only need a quick structural sketch. It uses
`gpt-5.5` (plan model) + `gpt-5.4` (execute model) but lacks the live-read
context and the plan→execute self-consistency of the frontier E2E path. Prefer
the frontier E2E path for any file >400 SLOC or with complex consumer graphs.

## Instructions

### 1. Identify Target (Flow A only)

If the user specified a source file, use it. Otherwise:

```bash
source ~/.venvs/universal/bin/activate
python scripts/modularize scan {path}
```

Read `tmp/modularize-scan.md`. Present red and yellow files. Ask which to
refactor (or accept "all red").

### 2. Cursor Builds Packet + Dispatches frontier Phase 1 (Flow A only)

Cursor does **not** call `scripts/modularize plan`. Cursor builds the packet
directly and dispatches frontier. Sequence:

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

1. Instruction for frontier to read the relevant cortex skills before code
   generation:

   ```
   Read the following skills before code generation:
   - cortex://agent-skills/architecture-invariants.md       (universal invariants)
   - cortex://agent-skills/ulg-architecture.md              (ULG topology + lifecycle)
   - cortex://agent-skills/modularize-discipline.md         (split-specific rules — SLOC ceilings, forbidden names, docstring requirement, change-scope, logger replacement)
   - cortex://agent-skills/implementation-plan-workflow.md  (plan-deck contract — BEFORE/AFTER completeness, density-tier mapping, recurring code-style friction)
   - cortex://agent-skills/frontier-model-instructions.md   (LLM-targeted prose discipline for the IMPLEMENTATION GUIDE)
   - cortex://agent-skills/prose-discipline.md              (audited-plan presentation prose to operator)
   ```

2. Any loaded workspace rules relevant to the file's domain (e.g. add the MCP
   relay invariant if it's an MCP tool handler; add the REST-namespace rule if
   it defines `/v1/*` endpoints; add the universal-protocol rule if it touches
   transport)

End the block with the **critical instruction to frontier**:

> "The `<invariants>` block is the sole architectural authority for this task.
> ¬ consult project docs, README files, or architecture docs to fill in
> gaps — they may be outdated or incomplete. If you encounter a pattern in
> the source that conflicts with the invariants, the invariants win. Flag the
> conflict rather than replicating the source pattern."

#### 2.2b Compose `<architecture>` block

The `<architecture>` block is a concrete before→after replacement table. Where
`<invariants>` states *rules*, `<architecture>` states *canonical replacements*
so frontier can perform corrective refactoring mechanically without inferring them
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

This scoped table prevents frontier from proposing spurious replacements for
patterns not present in the file, while giving it unambiguous mechanical
targets for those that are.

#### 2.3 Write packet file

`tmp/modularize-plans/{sanitized_name}-packet.md` — six-block format from
`architecture-handoff-protocol.mdc` plus one modularize-specific block. Per
`handoff-dispatchers.mdc` § frontier-mcp, the packet is partitioned across
system + user. Keep the **full packet text in one file** (so Phase 2 can
re-read it) and split at dispatch time.

| Block | Belongs in | Contents |
|---|---|---|
| `<scope>` | user | source path, git SHA, SLOC count, consumer count, one-line topic |
| `<invariants>` | system | the composed block from 2.2 |
| `<architecture>` | system | canonical before→after replacement table for invariant violations found in this codebase (see 2.2b) |
| `<mcp_capabilities>` | system | `frontier-mcp` template (frontier uses `fs` for reads/writes; `observability` for failure-mode verification) |
| `<output_format>` | system | "Reply with the modularization plan in MODULES + IMPLEMENTATION GUIDE + DEDUPLICATION OPPORTUNITIES sections, matching the legacy pipeline structure" |
| `<task_guidance>` (Phase 1) | user | "Read the source file at the path in `<scope>` live via `fs(sandbox='workspaces', op='read', ...)`. Read each consumer in `<corpus>` to verify which symbols are imported externally. Produce a complete modularization plan. Every proposed module name must pass the forbidden-name check in `<invariants>`. When the source contains any pattern listed in `<architecture>`, replace it with the canonical target — do not replicate the violation into new modules. Flag any other source patterns that conflict with `<invariants>` rather than replicating them."

**If MCP_CAPABLE = false** (non-MCP path — e.g. xai/grok-4.3):
Replace the live-read instructions with:

> "The source files and consumer import lines are pre-staged in `<corpus>` below.
> Use only the corpus contents — do not attempt `fs()` reads or live file access.
> 
> Completeness requirement: enumerate ALL of the following from the source files,
> without exception. Missing any item is a Phase 1 quality defect:
> - Every `_*_RE` regex constant defined in either source file
> - Every `_validate_*` and `_check_*` function
> - Every `_emit_*` and `_raise_*` helper
> - Every constant or function defined identically (or near-identically) in both files
> - Every test file that imports from the source modules
>
> Every proposed module name must pass the forbidden-name check in `<invariants>`."

**If MCP_CAPABLE = true** (MCP path — Anthropic models): keep the existing live `fs()` read instructions unchanged. |
| `<corpus>` | user | Two subsections: `## Source` (full file contents) and `## Consumers` (list of `path: import_lines` for each consumer found via grep) |

**Non-MCP corpus construction** (MCP_CAPABLE = false):
- `## Source — {filename}`: full file contents (inlined — model cannot do live reads)
- `## Consumers`: import lines only, not full file bodies. Format per consumer:
  ```
  path/to/consumer.py: from cortex_store.dispatch_ops.ops_journals import symbol_a, symbol_b
  ```
  Run: `rg "from.*ops_journals|import.*ops_journals" --type py` and capture the matching import lines.

**MCP corpus construction** (MCP_CAPABLE = true): keep existing approach (paths listed, model reads live). |

#### 2.3b Determine dispatcher path

**Phase 1 default model**: `openai/gpt-5.5` with `reasoning_effort="high"`. Do not substitute without explicit user direction.

Set `MCP_CAPABLE = model_family(model) == "anthropic"`.

| Model prefix | `MCP_CAPABLE` | Phase 1 path |
|---|---|---|
| `anthropic/` | true | Live `fs()` reads — model uses native MCP toolset |
| `openai/`, others | false | Inline corpus only — `fs()` instructions are a no-op; corpus is source of truth |
| `xai/grok-4.3` | false | **EXPERIMENTAL — NOT the default. See warning below.** |

This is derived automatically from the model ID — no `--model` flag needed.

> **WARNING**: grok-4.3 is hallucination-prone for Phase 1. In empirical testing (2026-05-16), a high-effort grok-4.3 run fabricated ~20 non-existent symbols (regex constants, functions) in its plan. These passed the §2.6 audit because the audit checks coverage of real symbols, not existence of listed ones. Do NOT use grok-4.3 for Phase 1 without the §2.6 Check 7 fabrication guard (see below).

#### 2.4 Dispatch Phase 1

```python
EXEC = frontier_dispatch(op="generate",
    messages=[{"role": "user", "content": <user-side blocks>}],
    model="openai/gpt-5.5",
    system=<system-side blocks>,
    reasoning_effort="high",
    caller_agent="cursor",
    max_tool_turns=15,
)
# Default Phase 1 model: openai/gpt-5.5 with reasoning_effort="high".
# xai/grok-4.3 is EXPERIMENTAL and NOT recommended for Phase 1 (see §2.3b warning).
# If using xai/* anyway: reasoning_effort="high" is required; "low" produces materially incomplete plans.
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
| `completed` | Extract `result["result"]["content"]` as frontier's plan |
| `failed` | Surface `result["result"]["error"]` verbatim. ¬ retry blindly |
| `pending`/`running` after ceiling | Save EXEC_ID; ask user to resume polling |

**Tool-loop exhaustion check**: if `status: completed` but `content == ""`,
frontier likely hit `max_tool_turns` mid-tool-loop. The dispatch envelope now
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

#### 2.6 Cursor audits frontier's plan

Mandatory checks before presenting to user:

1. **Module names**: flag forbidden filenames (`utils.py`, `helpers.py`,
   `common.py`, `misc.py`, `base.py`)
2. **Cross-module imports**: no direct `httpx` (must use `transport_utils`),
   no raw socket construction
3. **Public surface**: `init_reexports` should not expose internal helpers
   (names beginning with `_`) unless explicitly required by a consumer
4. **Logger violations**: no proposed `logging.getLogger(...)` declarations —
   `universal_logging.get_logger` only
5. **Dedup completeness**: every `_*_RE`, `_validate_*`, `_check_*`, `_emit_*`, `_raise_*` symbol present in the source files must appear in the plan's DEDUPLICATION OPPORTUNITIES or be explicitly noted as not duplicated. A plan that passes the first four checks but omits a regex constant or validation function present in both source files has a Phase 1 quality defect.
6. **Test consumer enumeration**: the plan must list any test file that imports from the source modules (found via grep for test files in §2.1) and include a step to update its imports.
7. **Symbol existence** (fabrication guard): for every symbol listed in the plan's DEDUPLICATION OPPORTUNITIES and MODULES sections, verify it actually exists in the source files. Run:
   ```
   rg "SYMBOL_NAME" libs/path/to/source1.py libs/path/to/source2.py
   ```
   for each symbol. Any symbol that returns no match is a hallucination — fail Phase 1 and re-dispatch.

   **Risk note**: This check is especially critical for non-Anthropic models (e.g. xai/grok-4.3). Empirical testing (2026-05-16) showed grok-4.3 @high fabricating ~20 non-existent symbols that passed Checks 1–6. gpt-5.5 is the reliable default for Phase 1.

If any check fails: pushback to frontier via re-dispatch with a `<prior_pass>`
block listing the audit failures, OR surface to user for direction.

If audit passes:
- Write frontier's plan to `tmp/modularize-plans/{name}.md` (compatible with
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
worktree: null
worktree_branch: null
grok_dispatch_id: null
---
```

**Schema** (canonical):

| Field | Type | Meaning |
|---|---|---|
| `source` | string | Original source file path |
| `git_sha` | string | HEAD at start of session — for stale-resume detection |
| `packet` | string | Path to packet file |
| `plan` | string | Path to frontier's plan |
| `phase` | `1` \| `2` \| `done` | Lifecycle stage |
| `last_completed_step` | int | IMPLEMENTATION GUIDE step number last verified complete |
| `files_pending` | list[string] | Paths frontier (or grok-build) still needs to write |
| `files_written` | list[string] | Paths confirmed on disk |
| `execution_ids` | list[string] | All frontier dispatch execution IDs (each redispatch appends) |
| `errors` | list[string] | Any failures encountered (transport, exhaustion, audit) |
| `worktree` | string \| null | Worktree path (`--grok` mode only; null if frontier mode) |
| `worktree_branch` | string \| null | Branch created for the worktree |
| `grok_dispatch_id` | string \| null | Last grok-build dispatch_id (`--grok` mode only) |

#### 2b.2 Build Phase 2 packet

System block (re-use Phase 1 system: invariants + **architecture** + mcp_capabilities + output_format).

User block:
- `<scope>`: same as Phase 1 plus a one-line note "Phase 2 — execute the plan"
- `<task_guidance>` (Phase 2):

  > "Before executing any step: verify the source file SHA matches the Phase 1 baseline.
  > Read `{source_path}` and confirm it has not changed since Phase 1 (git_sha in state file).
  > If HEAD SHA differs from `state.git_sha`, stop and surface to user — the corpus may be stale.
  >
  > You produced this plan in Phase 1. Execute it now using the IMPLEMENTATION
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

- `<corpus>`: `## Plan` (frontier's plan from Phase 1) + `## State` (current state
  file contents — for Flow C this carries the `<prior_pass>` info)
- `<prior_pass>` (Flow C only): "On a previous pass, the following IMPLEMENTATION
  GUIDE steps were completed and verified on disk: {step numbers + paths}.
  Do not re-write those files. Resume from step {last_completed_step + 1}.
  Read each previously-written file once before proceeding to confirm contents
  match your plan."

#### 2b.3 Dispatch Phase 2

**If `--grok` flag is NOT set (default — frontier E2E):**

```python
EXEC = frontier_dispatch(op="generate",
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

**If `--grok` flag IS set:** Replace frontier dispatch with grok-build worktree
execution (see §2b.3-grok below).

---

#### 2b.3-grok Phase 2 via grok-build (`--grok` flag)

When `--grok` is specified, Phase 2 uses grok-build in a git worktree.

##### 2b.3-grok.1 Create worktree

```python
# Create an isolated branch from HEAD
grokbuild(op="worktree_create",
           name="modularize-{sanitized_name}",
           branch="modularize/{sanitized_name}-{date}",
           source_repo="/mnt/torus/projects/universal-llm-gateway",
           create_branch=True)
# → envelope.metadata.worktree_path (e.g. /mnt/torus/projects/ulg-grok-worktrees/modularize-{name})
# Save worktree_path to state file as `worktree` field
# Save branch name to state file as `worktree_branch` field
```

##### 2b.3-grok.2 Build grok-build packet

Build the six-block packet per `agent-skills/grokbuild-v1.md` (canonical template; host path `/mnt/torus/mcp-data/files/agent-skills/grokbuild-v1.md`).

**`system_context` blocks:**

```
<scope>
Corpus and per-pass scope live in the prompt body.
Operate on files under cwd (the worktree). Do not write outside cwd.
</scope>

<invariants>
[universal:events]     Signal regex ^[a-z]+(\.[a-z]+){1,4}$. No underscores. @event_factory only.
[universal:logging]    from universal_logging import get_logger. Never stdlib logging.
[universal:no-bc]      Sole maintainer. No compat shims. No migration stubs. No deprecation aliases. Deleted = deleted.
[universal:mcp]        MCP tools are thin HTTP relays. Thin-subprocess wrappers permitted.
[universal:transport]  make_async_client() from transport_utils. No direct httpx socket construction.
[universal:async]      No subprocess.run() in async paths. Use asyncio.create_subprocess_exec().
[docs:no-vestigial]    Post-deletion docs/docstrings MUST NOT name retired surfaces.
[scope]                Only changed lines trace to the request. No opportunistic reformats.
[quality]              New modules ≤300 SLOC (scripts/modularize scan). ruff + compileall gates.
[workspace:naming]     No utils.py, helpers.py, managers.py, common.py. Name by domain.
This is the universal-llm-gateway workspace. Apply these invariants to all output.
</invariants>

<task_guidance>
Execute the modularization plan in the prompt. For each step in the IMPLEMENTATION GUIDE:
1. Read any source the step references via your filesystem tools.
2. Write the new file to cwd (the worktree).
3. Read the file back to verify the write landed with correct content.
4. Proceed to the next step.

When deleting the original source file: first read the package __init__.py to confirm all
required re-exports are in place. Only delete after __init__.py is verified.

Perform standard quality gates when the plan is complete:
- ruff check --select=UP --fix
- ruff format
- python -m compileall -q {newly-created package dir} {consumer paths}
- scripts/check-imports --stargate-entry {newly-created package dir}  (when under stargate)
- ruff check {all files written}

Record every action and final SLOC counts. End with a 'Files written' summary listing
every path you wrote, and quality gate outcomes (ruff, compileall, imports).
</task_guidance>

<mcp_capabilities>
You have grok CLI filesystem tools. No fs(sandbox=...), cortex, rag, observability, or agent_bus.
Filesystem visibility: /mnt/torus/projects/ (rw), /data/project/ (rw), /data/tasks/ (rw),
/data/files/ (rw), /data/cortex/ (rw), /data/rag-store/ (ro).
Read freely from any of these. Keep writes inside cwd (the worktree) so the audit captures them.
In read_only mode, narrate proposals without writing.
</mcp_capabilities>

<output_format>
Apply changes directly. In the final message:
- List files touched with one-line rationale per file
- Report SLOC before/after for each new module
- Report quality gate outcomes (ruff, compileall)
- Report any error that needs manual resolution
</output_format>
```

**`prompt` blocks:**

```
<scope>
Source: {source_path}
Worktree: {worktree_path}
Plan: {plan_path}
State: {state_path}
Phase: 2 (grok-build execution)
</scope>

<corpus>
/mnt/torus/projects/universal-llm-gateway/{plan_path}
/mnt/torus/projects/universal-llm-gateway/{state_path}
</corpus>

<task>
Execute the modularization plan at the corpus path. Apply every step in the IMPLEMENTATION GUIDE.
Write new modules under cwd. Update consumer imports. Run quality gates.
Report every file written and quality gate outcomes.
</task>
```

##### 2b.3-grok.3 Dispatch

```python
result = grokbuild(op="build",
    cwd=worktree_path,
    mode="edit",
    system_context=<system_context above>,
    prompt=<prompt above>,
    output_format="streaming-json")  # enables live monitoring via /tmp/logs/grokbuild/<id>.ndjson
```

Save `result["dispatch_id"]` to state file as `grok_dispatch_id`. Append worktree branch to state file as `worktree_branch`.

Turn budget: grok-build handles the full quality-gate loop internally — no `max_tool_turns` parameter (not applicable to grokbuild dispatch).

##### 2b.3-grok.4 Read result

```
result["status"] = "completed" ∧ result["metadata"]["read_only_violation"] = false
  ⇒ proceed to §2b.5
result["status"] = "completed" ∧ result["metadata"]["read_only_violation"] = true
  ⇒ inspect git_diff_stat; decide revert (git restore .) or accept
result["status"] = "failed"
  ⇒ surface result["stderr"] verbatim; offer re-dispatch or fallback to frontier Phase 2
result["status"] = "timeout"
  ⇒ inspect result["metadata"]["git_status_post"]; if files were written, continue to §2b.5 disk audit
```

---

#### 2b.4 Poll Phase 2 (frontier E2E mode only)

Same as 2.5 with the same status/exhaustion/error handling. Phase 2 typically
needs longer polling — re-poll up to 10 × 60s (10-minute ceiling) for large
files, since `fs` writes incur per-file roundtrip.

#### 2b.5 Update state file

**Frontier E2E mode — after frontier's response:**

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

**`--grok` mode — after grok-build result:**

Parse the "Files written" summary from `result["stdout"]`. Cross-check each path
on disk (via `Read` from Cursor). Move confirmed paths from `files_pending` to
`files_written`. Remove confirmed paths from `files_pending`. If `files_pending`
is non-empty: surface to user; they can re-run pointing at the state file.

##### 2b.5-grok.3 Merge worktree + remove

After disk audit confirms `files_pending` is empty:

```bash
# Merge the worktree branch into master
git -C /mnt/torus/projects/universal-llm-gateway merge {worktree_branch}
```

Then remove the worktree:

```python
grokbuild(op="worktree_remove", name="modularize-{sanitized_name}")
# rejects with worktree_dirty if uncommitted state remains
# rejects with worktree_busy if a dispatch is still in flight
```

Update state file: set `worktree` and `worktree_branch` to null; set `phase: done`.

### 3. Cursor Verifies

Once Phase 2 reports complete and `files_pending == []`:

```bash
# Compile every newly-created file plus every consumer
python -m compileall -q {newly-created package dir} {consumer paths}

# Import resolution (mandatory — compileall does not execute imports)
scripts/check-imports --stargate-entry {newly-created package dir}

# Lint check
ruff check {all files written by frontier or grok-build} {consumer files modified}
```

If compileall or check-imports fails:
- Read the failure to find the broken import
- Either fix manually (single-line typos) or re-dispatch frontier Phase 2 with a
  `<prior_pass>` block describing the failure (let frontier fix what it broke)

If ruff fails: re-dispatch frontier with the lint output, OR run
`ruff check --fix && ruff format` if the failure is autofixable.

Update state file: `phase: done` once compile + imports + lint all pass.

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
- Dispatcher:
  - Default mode: `frontier E2E (openai/gpt-5.5 × 2 phases)`
  - `--grok` mode: `frontier Phase 1 (openai/gpt-5.5) + grok-build Phase 2 (worktree: {worktree_branch})`
- Tech debt addressed (legacy `logging.getLogger` calls replaced, etc.)
- State file path (kept for audit; remove only if user requests)

## Failure Handling

Per `handoff-dispatchers.mdc` § frontier-mcp "Failure handling":

| Failure | Reaction |
|---|---|
| Stargate transport error before dispatch | Stop. Surface error. Offer (1) start Stargate and rerun, (2) fall back to `web-claude` dispatcher (manual). ¬ local execution. |
| Tool-resolution error (`result.error` references unresolved tool) | Stop. Report `result.error` verbatim. Most likely cause: `MCP_PUBLIC_URL`/`MCP_AUTH_TOKEN` not configured on Stargate. |
| Tool-loop exhausted (Phase 1 or 2, frontier mode) | Re-dispatch once with `max_tool_turns=25` (Phase 1) or `max_tool_turns=60` (Phase 2 — bumped from the §2b.3 default of 40). Confirm via `pipeline-trace` (`pipeline.frontier.dispatch.exhausted` with `enforcement` ∈ {`client`, `provider`}). After second exhaustion, surface and stop. |
| `status: failed` (frontier) | Report `result.error` verbatim. Offer fall-back to `web-claude`. |
| Phase 1 audit failure (forbidden filename, public-surface leak) | Re-dispatch frontier with `<prior_pass>` listing the audit findings. ¬ apply blindly; ¬ silently rename. |
| Phase 2 partial completion | State file captures `files_written`. User can resume by re-running command with state file path (Flow C). |
| grok-build Phase 2 failed (`status: failed`, `--grok` mode) | Surface `result["stderr"]` verbatim. Offer (1) re-dispatch grok-build with a corrected prompt, (2) fall back to frontier Phase 2 (default mode). |
| grok-build `read_only_violation=true` | Inspect `git_diff_stat`; revert with `git restore .` if unexpected writes are present, or accept if the diff matches expectations. |
| grok-build `worktree_busy` / `worktree_dirty` on remove | Check for in-flight dispatches or uncommitted changes; resolve before retrying `worktree_remove`. |

## Rules

- ¬ execute Phase 2 without explicit user approval after Phase 1 audit
- ¬ skip the audit (rule-blind reviewer rule applies — frontier does not see
  `.mdc` rules, only the `<invariants>` block you compose)
- ¬ deviate from frontier's own plan structure during Phase 2 — if the plan is
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

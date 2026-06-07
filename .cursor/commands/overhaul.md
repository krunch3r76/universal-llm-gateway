Per-directory code overhaul: modularize, review, fix, docstring, verify.
Orchestrates the full quality pass for a single directory.

## Usage

```
/overhaul {directory}
/overhaul frontier {directory}
```

Where `{directory}` is a path relative to the project root (e.g.,
`services/universal-stargate/systems/proxy/`). When the first argument is
`frontier`, use the automated posture (see Operating posture table); otherwise
use gradual (default).

If you need to unblock a single oversized file first, use:

```
/overhaul-file {file}
```

Then return to `/overhaul {directory}` for the full subsystem pass.

Optional automated path (requires working `team-generate` / Stargate frontier dispatch):

```
/overhaul frontier {directory}
```

Use `frontier` only when frontier dispatch is verified end-to-end. Otherwise stay
on the default gradual posture below.

## Operating posture (default: gradual)

The default `/overhaul` run is **checkpoint-driven** and **web-claude-first**.
Cursor orchestrates; external reasoning goes to Claude Web via `agent_bus`; Stargate
pipelines run only when the user explicitly approves each call.

| Concern | Gradual (default) | Automated (`frontier` mode) |
|---|---|---|
| Deep file splits | web-claude handoff (plan on thread; Cursor applies) | `/modularize` (team-generate E2E) |
| Code review | web-claude handoff (scoped packet) | `/consult-review` (`code-review` pipeline) |
| Architecture doc review | `/review-arch-doc` (web-claude default) | `/review-arch-doc team-generate` |
| Bulk split plans | `scripts/modularize plan` — **one file at a time, user approves each run** | same, batched when user directs |
| Doc generation | step 9 — user approves before `doc-generate` call | same |

**Checkpoint gates** — stop and summarize after each block; do not advance until
the user confirms:

1. After steps 1–1.5 (scan + vulture) — review file list and dead-code deletions
2. After step 2 — review split plans before any edits or web-claude dispatch
3. After step 3 — review applied splits before external review
4. After step 4 — review findings before fixes
5. After steps 5–8 — review docstring/gate/rescan status before doc generation
6. After steps 9–11 — review generated doc + web-claude findings before commit

**web-claude dispatch invariant**: substantive content lives in workspace packet
files under `tmp/`; `agent_bus` posts are ≤25-line pointers only. See
`projects/.cursor/rules/handoff-dispatchers.mdc` § `web-claude`.

## Instructions

Execute these steps in order. Each step must complete before moving to the next.
Honor the checkpoint gates above. Get user approval before applying changes at
step 3. Step 4 manages its own approval flow per the chosen review path.

### 1. Scan for SLOC violations

```bash
source ~/.venvs/universal/bin/activate
scripts/modularize scan {directory}
```

Note any red (>400) or yellow (301-400) files.

### 1.5. Scan for cross-file dead code

```bash
vulture {directory} vulture_whitelist.py --min-confidence 80
```

Review findings. Delete genuinely dead code (unused functions, unreachable
branches, dead exports) before proceeding — removing dead code first means
the pipelines analyze cleaner files and don't waste tokens on code about
to be deleted.

Known false positives: `getattr()` dispatch, FastAPI route handlers, event
handler callbacks, `__init__.py` re-exports. Add confirmed false positives
to `vulture_whitelist.py`.

### 2. Generate split plans for oversized files

For each file flagged red or yellow in step 1, choose a tier. In gradual mode,
process **one file at a time** and ask before running any pipeline or posting
to web-claude.


| Tier | Tool | When to use |
|---|---|---|
| **Manual** (gradual first) | Cursor reads file + consumers; user-directed split | Yellow (301–400), simple structure, ≤2 consumers — skip pipelines entirely |
| **Bulk** | `scripts/modularize plan {file}` (Stargate `modularize` pipeline) | File ≤600 SLOC, simple consumer graph — **user approves each `plan` invocation** |
| **Deep** (gradual default) | web-claude modularize handoff (see §2.1) | Bulk plan has coverage warnings, PHANTOM symbols, invariant violations, complex consumers, or file >600 SLOC |
| **Deep** (`frontier` mode only) | `/modularize {file}` (team-generate E2E) | Same triggers as deep web-claude — use only under `/overhaul frontier` |

The bulk tier runs the modularize pipeline (analyze → critique → finalize) via
Stargate (`POST /v1/chat/completions`), model `modularize`. Cheap but lacks live
consumer reads. **Do not batch** bulk plans across files in gradual mode unless
the user explicitly requests it.

The deep tier (gradual) posts a six-block packet to Claude Web per
`/modularize` steps 2.1–2.3 and `projects/.cursor/rules/handoff-dispatchers.mdc` § `web-claude`.
Claude Web plans with live `fs` reads; **Cursor applies** the approved plan
locally (no frontier Phase 2 auto-execution).

If unsure in gradual mode: try **Manual** for yellow files, **Bulk** for straightforward
red files, **web-claude** when bulk output is incomplete or untrusted.

### 2.1. Deep tier — web-claude modularize handoff (gradual default)

When bulk is insufficient or skipped, build and post a modularize packet instead
of calling `/modularize`:

1. Gather artifacts per `/modularize` §2.1–2.2 (source, consumers via grep, composed `<invariants>`, `<architecture>` replacement table for violations in this file). The composed `<invariants>` block per /modularize §2.2 references these cortex skills (the web-claude dispatcher loads them on receipt):
   - `cortex://agent-skills/architecture-invariants.md` (universal)
   - `cortex://agent-skills/ulg-architecture.md` (ULG-specific)
   - `cortex://agent-skills/modularize-discipline.md` (split-specific rules)
   - `cortex://agent-skills/implementation-plan-workflow.md` (plan-deck contract)
   - `cortex://agent-skills/frontier-model-instructions.md` (LLM-targeted prose)
   - `cortex://agent-skills/prose-discipline.md` (operator-facing summary prose)
2. Write `tmp/modularize-plans/{sanitized-name}-packet.md` — six-block format from `architecture-handoff-protocol.mdc` (same block table as `/modularize` §2.3).
3. Add packet frontmatter for Claude Web boot gate:
   - `active_project_tag: project:<repo>`
   - `cortex_boot_confirmed: true|false`
   - `related_thread_ids: [...]` when applicable
4. Post pointer via `agent_bus(tool="post", to="claude-web", ...)` — body ≤25 lines; points at packet path only.
5. **Wait** for Claude Web reply on the thread. Do not re-post unprompted.
6. Audit the returned plan per `/modularize` §2.6 (forbidden filenames, cross-module imports, public surface, logger violations).
7. Present audited plan + package-shadow tree; get user approval before step 3 apply.

For execution-phase iteration, reply on the same thread with `<prior_pass>` context
rather than opening a new dispatch. Close with `agent_bus(tool="close", ...)` when
the split is complete.

Under `/overhaul frontier`, replace steps 1–7 above with `/modularize {file}`.

### 2.5. Pipeline model configuration

The bulk pipeline's `plan_model` and `execute_model` live in
`pipelines/modularize/models.yaml`. The current values are tuned defaults; do
not auto-update them as part of overhaul. Update them through a focused
consultation cycle on the `pipelines/modularize` subsystem when a clearly
stronger candidate is empirically demonstrated on this codebase, not from a
public leaderboard.

The deep tier under `/overhaul frontier` (`/modularize`) hardcodes `openai/gpt-5.5` per
`projects/.cursor/rules/handoff-dispatchers.mdc` and is not affected by `pipelines/modularize/models.yaml`.
The web-claude deep tier is not affected by `models.yaml` either.

If a service restart is required after editing pipeline model configs, ask the
user to run `./manage` — do not start/stop services directly.

Present each plan to the user for review.

For each oversized file plan, also show a proposed package-shadow directory
tree before requesting step 3 approval.

Example format:

```text
path/to/target/
  __init__.py
  module_a.py
  module_b.py
```

Requirements:

- Show one tree per planned split target file.
- Include all planned submodules and `__init__.py` re-export surface.
- Mark uncertain symbol placement explicitly; resolve with user before edits.

### 3. Apply split plans

For each approved split plan, implement the module extraction:

- Default to **package-shadow** layout for file splits:
  - `path/to/target.py` -> `path/to/target/` package directory
  - split modules live under `path/to/target/*.py`
  - `path/to/target/__init__.py` re-exports preserve the public import surface
  - remove original `path/to/target.py` after package exports are in place
- Create the new module files as specified
- Move definitions to their target modules
- Update imports across all consumers
- Create `__init__.py` with re-exports if needed

If a generated plan has coverage warnings, PHANTOM symbols, or is otherwise
underspecified, **escalate to the deep tier** instead of asking the user to
approve a partial bulk plan:

- **Gradual (default)**: web-claude handoff per §2.1
- **`frontier` mode**: `/modularize {file}`

Do not approve and apply a partial bulk plan when deep escalation is indicated.

For plans that look complete and consistent, proceed with the bulk apply
under user approval.

**Requires user approval** before applying each split.

### 4. Code review (choose path)

**Gradual (default) — web-claude review handoff**

Scope files in `{directory}`:

```bash
git diff --name-only -- {directory}
find {directory} -name '*.py' -not -path '*/__pycache__/*' | sort
```

Build a review packet per `architecture-handoff-protocol.mdc` and
`projects/.cursor/rules/handoff-dispatchers.mdc` § `web-claude` (pointer-only
post). Skeleton: `universal-llm-gateway/tmp/reviews/_handoff-packet-template.md`;
transport: `agent-skills/consult-routing.md`.
Write to `tmp/reviews/overhaul-{subsystem}-claude-web-packet.md`. Include:

- `<scope>`: directory path, git SHA, file list
- `<invariants>`: composed workspace rules relevant to this subsystem, plus cortex skill references for the web-claude reviewer:
  - `cortex://agent-skills/architecture-invariants.md` (universal — baseline)
  - `cortex://agent-skills/ulg-architecture.md` (ULG-specific — baseline)
  - `cortex://agent-skills/evidence-review-discipline.md` (pre-assert skeptic pass; findings anchored on specific lines, not pattern-match)
  - `cortex://agent-skills/frontier-reasoning-discipline.md` (steelman before critique; calibrated confidence on severity)
  - `cortex://agent-skills/no-silent-inference.md` (verify or mark; don't upgrade inference to fact)
  - `cortex://agent-skills/engagement-stance.md` (substantive, not performative; don't soften real Critical to Warning)
- `<corpus>`: changed-file manifest (paths only — Claude Web reads via `fs`)
- `<task_guidance>`: post-overhaul split review — correctness, invariants, event
  coverage gaps, docstring quality, observability noise (see §4.5)
- `<output_format>`: severity-grouped findings with `Evidence:` per finding

Post to `claude-web`; wait for thread reply. Triage findings into Applied /
Pending / Rejected / Suggestions. Apply Critical only after validation; ask
before Warning or Suggestion items.

Alternatively, run `/diff-review web-claude` scoped to the same file set when
that command's manifest workflow fits better.

**`frontier` mode — pipeline review**

Execute the full `/consult-review` workflow on the directory's Python files.
This handles pre-flight pipeline availability checks, SLOC gates, batching,
invariant validation, event coverage gap detection, and the closing checklist.

Use the same scope commands as above, then follow `/consult-review` instructions.

The closing checklist (Applied / Pending / Rejected / Suggestions) replaces
manual finding triage. All fixes require user approval — do not auto-apply
Warning or Suggestion items.

Event coverage gaps surfaced by the review pipeline are handled within
`/consult-review` (see its Event Coverage Gaps → Suggestions section).
New signals follow `docs/event-contracts.md` conventions.

### 4.5. Observability-first noise reduction

Apply this policy across touched files during overhaul:

- Lean on structured events for request-path observability; avoid verbose
per-request/per-candidate logs at `info` level when event coverage exists.
- Demote repetitive branch diagnostics to `debug`; keep `info`/`warning` for
actionable boundaries and operator-relevant summaries.
- Preserve one concise summary log per major success/failure branch where
useful for quick local triage.
- If a noisy log is reduced or removed, ensure equivalent (or better) event
signal coverage remains.

When generating/applying review fixes, treat these as high-value event
opportunities before introducing new logs:

- Decision/branch outcome events with explicit reason fields
- Retryable vs non-retryable failure boundary events
- Queue/backpressure lifecycle events (enter/wake/timeout/cancel)
- Spillover/fallback/handoff transition events
- Invariant guard-block events (condition blocked action)
- Recovery/unblocked events after prior failure states

### 5. Docstring pass

Ensure every module, class, and public function has a docstring that meets
these quality standards. The doc-generate pipeline (step 9) extracts
docstrings verbatim via tree-sitter — thin docstrings produce thin
architecture docs.

#### Quality standard

**Module docstrings** (≥15 words): What the module does, who calls it, key
invariants or design decisions.

Good:

```
"""Request routing and gateway selection for federated inference.

Implements the core routing algorithm that selects which gateway should
handle an incoming request. Called by the proxy layer after model ID
resolution. Routing decisions are based on model availability, capacity
constraints, and latency preferences.

Invariant: never routes to a gateway that lacks the requested model or
has exhausted its capacity budget.
"""
```

Bad:

```
"""Request routing module."""
```

```
"""Handles routing."""
```

**Class docstrings** (≥15 words): Purpose, lifecycle (how/when created
and destroyed), key methods worth knowing about.

Good:

```
"""Tracks in-flight requests and enforces per-gateway capacity limits.

Created once per Stargate instance at startup. Maintains a concurrent
map of active requests keyed by (gateway_id, model_id). Capacity is
released when the request completes or times out.

Key methods:
    acquire(): Reserve a capacity slot, blocking if at limit.
    release(): Free a capacity slot after request completion.
    snapshot(): Return current utilization for telemetry.
"""
```

Bad:

```
"""Capacity tracker."""
```

```
"""Class for tracking capacity."""
```

**Function docstrings** (≥10 words): What the function does (not just
restating the name), parameter semantics when non-obvious, return value,
side effects (event emissions, state mutations, I/O).

Good:

```
def resolve_model_id(raw_id: str, aliases: dict[str, str]) -> ModelId:
    """Normalize and resolve a raw model identifier.

    Handles alias expansion, version suffix stripping, and quantization
    tag normalization. If raw_id matches an alias key, the alias target
    is used before normalization.

    Returns a validated ModelId or raises ModelIdError if the format
    is unrecognizable after all normalization attempts.
    """
```

Bad:

```
def resolve_model_id(raw_id, aliases):
    """Resolve model ID."""
```

```
def resolve_model_id(raw_id, aliases):
    """Resolves the model id from raw id and aliases."""
```

#### Audiences

Written for three consumers:

1. **Humans** reading code — explain the "why", not just the "what"
2. **Agents** navigating the codebase — name the callers, invariants,
  and relationships so an LLM can reason about dependencies
3. **Embedding models** chunking for RAG — use distinctive terms that
  differentiate this module/class/function from similar ones

#### Scope

Skip private helpers (`_name`) unless their logic is non-obvious.
For `__init__.py` files, a brief re-export summary is sufficient.

### 5.5. Verify docstring quality

Run the docstring quality checker:

```bash
source ~/.venvs/universal/bin/activate
scripts/docstring-quality scan {directory}
```

This checks every module, public class, and public function for:

- **empty** (critical): No docstring at all
- **too_short** (warning): Below word count threshold for scope
- **name_echo** (warning): First sentence just restates the element name

If there are critical issues (exit code 1), fix them and re-run before
proceeding. For warnings, review the report and improve any docstrings
that would produce thin architecture doc sections.

The goal: every docstring should give doc-generate enough material to
write a substantive architecture doc paragraph, not just a label.

### 5.6. Optional cloud docstring enhancement pass

For directories where local/manual docstring cleanup still leaves thin content,
run a second pass through the prompt-tunable `docstring-enhance` pipeline:

```bash
/docstring-enhance {directory}
```

Use this when:

- warnings remain concentrated on module/class/function quality (not missing files)
- generated architecture drafts still produce weak sections or repeated HUMAN markers
- you want prompt-level iteration without editing command logic

Pipeline tuning surface:

- prompts: `pipelines/docstring_enhance/prompts.yaml`
- models: `pipelines/docstring_enhance/models.yaml`

After applying pipeline proposals, re-run step 5.5 before proceeding to
quality gates and doc generation.

### 6. Quality gates

```bash
source ~/.venvs/universal/bin/activate
ruff check --select=UP --fix {directory}
ruff format {directory}
python -m compileall -q {directory}
ruff check {directory}
```

**Import resolution (mandatory).** `compileall` checks syntax only — it does
not execute imports. After any split, package-shadow move, or relative-import
change under `services/universal-stargate/`, run:

```bash
scripts/check-imports --stargate-entry {directory}
```

For `libs/` changes:

```bash
scripts/check-imports libs/{package}/
```

When `{directory}` touches Stargate source, also verify the service entry
point loads (included automatically by `--stargate-entry`):

```bash
# imports systems.proxy.app — same path as start_proxy.py
```

`quality_gate(files=[...])` runs the same import check when MCP is available.
**Invariant:** compileall pass ≠ imports pass. Do not commit or call
`sync_restart stargate` until `check-imports` exits 0.

For Stargate-touching changes, run post-apply (after step 3 or step 6):

```bash
manage(action="sync_restart", service="stargate")
manage(action="wait_healthy", service="stargate", timeout=120)
```

Per `service-lifecycle` skill — source under `services/universal-stargate/`
requires deploy verification, not just static gates.

### 7. Unused imports

```bash
ruff check --select F401 {directory}
```

Fix any remaining unused imports.

### 8. Re-scan

```bash
scripts/modularize scan {directory}
```

Verify all files are green (≤300 SLOC).

For any file still yellow/red after the bulk pass, **escalate to the deep tier
per file**:

- **Gradual (default)**: web-claude handoff per §2.1
- **`frontier` mode**: `/modularize {still-red-file}`

Do not loop the bulk pipeline a second time on the same file — if it didn't
land cleanly the first time, escalate to web-claude (or `/modularize` under
`frontier` mode). Re-run step 8 after each deep split completes; continue until
all files are green or the user explicitly defers a remaining violator.

### 9. Generate/update architecture doc

**Gradual gate**: summarize steps 1–8 outcomes and ask the user before invoking
`doc-generate`. Skip this step entirely if the user defers architecture doc work.

Verify the `doc-generate` pipeline is available:

```bash
curl -s http://localhost:9999/v1/models | jq '.data[] | select(.id == "doc-generate")'
```

If no result is returned, stop and ask the user to ensure Stargate is running
and has loaded the latest pipeline config before continuing.

Run doc-generate and capture raw response:

```bash
DIRECTORY_ABS="$(realpath "{directory}")"
DOC_GEN_RESPONSE="$(curl -s -X POST http://localhost:9999/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"doc-generate\",\"messages\":[{\"role\":\"user\",\"content\":\"${DIRECTORY_ABS}\"}]}")"
echo "$DOC_GEN_RESPONSE" > /tmp/doc-generate-response.json
```

Extract the JSON payload from the assistant response:

```bash
DOC_JSON="$(echo "$DOC_GEN_RESPONSE" | jq -r '.choices[0].message.content // empty')"
echo "$DOC_JSON" | jq . > /tmp/doc-generate-result.json
```

If extraction/parsing fails:

- save `/tmp/doc-generate-response.json`
- report the failure to the user
- do not continue to commit until step 9 succeeds

From `/tmp/doc-generate-result.json`, extract:

- `doc_markdown`
- `unsupported_claims`
- `missing_coverage`
- `human_markers`

Write generated markdown to:

```bash
SUBSYSTEM="$(basename "{directory%/}")"
DOC_PATH="docs/architecture/${SUBSYSTEM}.md"
echo "$DOC_JSON" | jq -r '.doc_markdown' > "${DOC_PATH}.generated"
```

### 10. Review generated doc

Review `${DOC_PATH}.generated` before replacing `${DOC_PATH}`:

1. Verify every factual claim traces to extracted docstrings/signatures.
2. Resolve all `unsupported_claims` and `missing_coverage` items from
  `/tmp/doc-generate-result.json`.
3. Address each `<!-- HUMAN: ... -->` marker with authored content where needed.
4. Ensure generated sections remain wrapped by:
  - `<!-- GENERATED:START -->`
  - `<!-- GENERATED:END -->`
5. Preserve authored sections marked by `<!-- AUTHORED -->` where applicable.

When review passes:

```bash
mv "${DOC_PATH}.generated" "${DOC_PATH}"
```

### 11. web-claude review of generated doc (gradual default)

Run `/review-arch-doc` on the generated architecture doc. **Gradual mode omits
the dispatcher argument** — `/review-arch-doc` defaults to `web-claude`.

```bash
/review-arch-doc "${DOC_PATH}" /tmp/doc-generate-result.json --source {directory}
```

Claude Web reads live source via MCP, replies on the agent_bus thread. Follow
`/review-arch-doc`'s validation contract before applying any finding:

1. Apply Critical findings that survive local rule validation.
2. Ask the user before applying Warning or Suggestion findings.
3. Reject findings that contradict the extraction inventory or workspace rules.
4. Record surfaced/deferred findings in the review artifact.

**`frontier` mode** — synchronous automated review when frontier dispatch is verified:

```bash
/review-arch-doc team-generate "${DOC_PATH}" /tmp/doc-generate-result.json --source {directory}
```

If `team-generate` fails, do not silently fall back. Offer the user:

- retry later (Stargate may recover)
- continue with web-claude (gradual default)
- `local-self` (in-context review, no external dispatcher)

Proceed only with the user's chosen path.

### 12. Commit code + docs together

Stage both code and architecture doc updates in one commit:

```bash
git add {directory} "${DOC_PATH}"
git commit -m "overhaul: {directory} (code + architecture doc)"
```

Do not split code and doc updates into separate commits.

## Rules

- Default posture is **gradual / web-claude-first**; use `/overhaul frontier` only
  when team-generate is verified end-to-end
- Honor checkpoint gates — ¬ advance phases without user confirmation in gradual mode
- ¬ invoke Stargate pipelines (`modularize plan`, `code-review`, `doc-generate`) without
  explicit user approval per call in gradual mode
- ¬ start the overhaul before verifying Stargate is running when a pipeline step is
  approved (`code-review` under `frontier` mode; `doc-generate` at step 9)
- web-claude steps require agent_bus relay — ¬ inline packet content in thread posts
- ¬ modify `scripts/modularize` — it works as-is, this command calls it
- ¬ apply suggestion-level findings without explicit user instruction
- ¬ skip the docstring pass — it directly improves RAG retrieval quality
- ¬ skip docstring quality verification at step 5.5 — thin docstrings produce thin architecture docs
- ¬ skip the re-scan — final verification ensures no regressions
- Step 11 gradual default: `/review-arch-doc` (web-claude); `team-generate` only under
  `/overhaul frontier`; do not use `scripts/consult-frontier` for generated architecture docs
- Process one directory at a time — do not batch multiple directories
- Prefer event signals over high-noise request-path logs during refactors


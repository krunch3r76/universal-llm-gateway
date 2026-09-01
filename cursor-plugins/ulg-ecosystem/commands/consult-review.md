Post-implementation review via the code-review pipeline. Gathers unstaged/untracked
source files from git status, sends them to the `code-review` pipeline for structured analysis,
and presents a prioritised list of issues with concrete fixes.

## When to Use

Before committing changes that:
- Touch more than 3 files, or
- Span more than one subsystem, or
- Introduce a new module, new public API, or new event signal, or
- You have any doubt about correctness, races, or invariant preservation

## Instructions

### Follow-up Intent Aliases (apply phase)

Interpret user follow-up intents with these aliases:

- **Apply all findings (full sweep)**:
  - Trigger phrases include:
    - `Apply all review findings: critical + warnings + suggestions, everywhere in scope.`
    - `Apply all findings`
    - `Apply all recommended fixes`
    - `Apply all suggestions` (treat as full sweep by default in this workspace)
  - Behavior:
    1. Resolve scope to **all consult-review findings artifacts from the current session directory**:
       - `./tmp/consult-review-sessions/{SESSION_ID}/code-review-batch-*.json`
       - `./tmp/consult-review-sessions/{SESSION_ID}/consult-review-*.json`
       - `./tmp/consult-review-sessions/{SESSION_ID}/code-review-report.md` (closing checklist + pending items)
    2. Aggregate + de-duplicate findings across those artifacts, then apply validated
       **Critical + Warning + Suggestion** items across all reviewed files in scope.
    3. Include findings from solo over-SLOC/oversized review batches (override submissions).
    4. If a finding conflicts with invariants, do **not** apply it by default.
    5. Explicitly list not-applied items and why.

  - Optional (user-directed only, not default):
    - If user explicitly asks to adapt invariant-conflicting findings, apply the
      closest rule-compliant equivalent and report the adaptation.

- **Suggestions only**:
  - User must be explicit, e.g. `apply suggestion-only` or `apply suggestions only (not warnings/critical)`.

### 1. Identify Changed Files

**Scope policy (hard requirement): review unstaged + untracked files only.**

### 1a. Create a Unique Session Directory (MANDATORY)

Before collecting files or submitting any batch, create a unique session directory
under `./tmp/` and use it for **all** review artifacts in this run:

```bash
SESSION_ID="$(date +%Y%m%d-%H%M%S)-$(python3 - <<'PY'
import uuid
print(uuid.uuid4().hex[:8])
PY
)"
SESSION_DIR="./tmp/consult-review-sessions/${SESSION_ID}"
mkdir -p "${SESSION_DIR}"
echo "Consult-review session: ${SESSION_DIR}"
```

Persist and reuse `${SESSION_DIR}` for every payload, batch result, synthesized
report, and implementation-tracking artifact in the same `/consult-review` run.
Do not read artifacts from sibling session directories.

```bash
git diff --name-only
git ls-files --others --exclude-standard
```

Do **not** use `git diff --name-only HEAD` for this command; it includes staged
changes and violates the unstaged-only scope policy.

If `git diff --name-only` returns nothing, continue with untracked files only:
```bash
git ls-files --others --exclude-standard
```

Collect the union of unstaged tracked files and untracked files. Filter to only
source files (`.py`, `.yaml`).
Ignore lockfiles, generated files, `./tmp/`, and `docs/architecture/` (curated
separately — see `tmp/prompts/overhaul.md` § Documentation curation).

**Docstring quality emphasis (aligned with overhaul goals):**
- Treat docstrings as architecture inputs, not optional prose.
- Review changed modules/classes/public functions for substantive docstrings:
  - module/class docstrings target ≥15 words
  - public function docstrings target ≥10 words
- Reject placeholder or name-echo text (for example: "Handles routing", "Capacity tracker").
- Favor docstrings that capture caller context, invariants, and side effects so
  generated architecture docs remain specific and useful.

### 1b. Verify Source Quality (MANDATORY)

**This step is mandatory. Do not skip it or defer it to post-review.**

Run quality gates on the collected `.py` files before submitting to the pipeline.
Files that fail compilation or have import errors waste pipeline capacity and
produce noise findings that obscure real issues.

```bash
# Compilation check — catches syntax errors and structural import issues
python -m compileall -q {PY_FILES}

# Lint check — catches unused imports (F401), undefined names (F821), syntax errors (E999)
ruff check --select=F401,F821,E999 {PY_FILES}
```

**If either check fails — STOP.**

Present the errors to the user and ask whether to:
1. Fix the errors first, then re-run `/consult-review`
2. Proceed anyway (user override — record that pre-flight failed in the report)

**Do not silently proceed past failing verification.** The pipeline models cannot
distinguish pre-existing import errors from review-worthy issues, so broken
imports pollute every batch they appear in.

Run an event-signal literal gate on changed source files before pipeline submission:

```bash
rg -n 'signal\s*=\s*"[^"]*[_-][^"]*"' {SOURCE_FILES}
```

If any match is found — STOP and fix signal naming before review:
- Signal segments must be lowercase alpha and dot-separated
- Required format: `^[a-z]+(\.[a-z]+){1,4}$`
- No underscores or hyphens in signal segments

If any changed file adds or modifies event factories, event emissions, `signal=`,
`role=`, or `scope=`, run a mandatory event-contract audit before submission.
Treat this as a hard gate, not an optional review topic.

Event-contract audit checklist:

1. **Factory enforcement** — no raw `Event(...)` construction outside approved
   factory files/functions. Run:
   ```bash
   python scripts/validate-event-factories.py
   ```
2. **Factory usage pattern** — new or changed Event-returning helpers use
   `@event_factory` and follow existing subsystem factory conventions.
3. **Payload schema** — compare changed signals against `docs/event-contracts.md`
   and the nearest subsystem event module. Required payload fields must exist,
   field names must match the contract, and emitted values must come from real
   runtime data (no placeholders or TODO payloads).
4. **Classification** — `role` and `scope` are explicitly correct for the
   signal's semantics:
   - `coordination` for signals consumed by state machines, admission control,
     queues, or lifecycle release/wake logic
   - `observation` otherwise
   - `node` only when the signal is meaningful strictly at the originating node
   - `global` otherwise
5. **Contract sync** — new or changed signals must update
   `docs/event-contracts.md` in the same change. The catalog table regions are
   generated from `@event_factory` call sites — edit the factory and regenerate,
   never hand-edit inside `<!-- GENERATED -->` markers. Verify code↔doc parity:
   ```bash
   gen-event-catalog --check
   ```
6. **Emission boundary** — validate that the emission point is the correct
   control-flow edge (decision, retry, failure, recovery, wake, release), not
   merely "nearby code that had the right variables available".

**If any event-contract audit check fails — STOP.**

Present the failures to the user and ask whether to:
1. Fix the event contract issues first, then re-run `/consult-review`
2. Proceed anyway (user override — record that event pre-flight failed in the report)

If the user chooses option 2, note in the submission plan (step 3d) which files
had pre-flight failures, including event-contract failures, so findings on those
files can be triaged accordingly.

---

### 2. Verify Pipeline Availability

**This step is mandatory and must complete successfully before proceeding.**

Model preference for this workspace:
- Default reviewer: `google/gemini-2.5-flash`
- Reliability fallback for large/timeout-prone batches: native OpenAI reviewer
  (for example `native/chatgpt/gpt-5.3-codex`)
- Use native Anthropic only as a last resort due to cost.

Check whether the `code-review` pipeline is registered and available:

```bash
curl -s http://localhost:9999/v1/models | jq -r '.data[]?.id' | grep -x "code-review"
```

**If `code-review` is NOT in the output — STOP.**

Do not proceed to batching or submission. Do not fall back to a manual review.

Instead, diagnose and surface the reason:

```bash
# Find which models the pipeline needs but can't resolve
jq -c 'select(.signal == "pipeline.registry.unavailable" and .payload.pipeline_id == "code-review") | .payload' \
  /tmp/stargate-events/current.jsonl | tail -3

# Check cloud proxy health
jq -c 'select(.signal | test("cloud.proxy.started|cloud.proxy.unavailable|cloud.proxy.shutdown"))' \
  /tmp/cloud-proxy-events/current.jsonl | tail -5
```

Then present the user with a clear stop message:

```
⛔ /consult-review cannot run: the `code-review` pipeline is not available.

  Missing models: {list from pipeline.registry.unavailable event}
  Cloud proxy: {started / unavailable / not running}

  Required actions before retrying:
  1. Start the cloud proxy via `./manage` (if not running or health-probe failed)
  2. Confirm cloud models are visible: curl -s http://localhost:9999/v1/models | jq -r '.data[]?.id' | grep "google/\|mistralai/"
  3. Wait for Stargate to auto-reload pipelines (watch for pipeline.registry.unavailable
     disappearing from events), or restart Stargate via `./manage`
  4. Re-run /consult-review
```

**Do not proceed past this step until the `code-review` model appears in `/v1/models`.**

---

### 2.5. Gather Architecture Context (DISABLED)

Architecture context injection is disabled for now — docs are outdated and not
maintained automatically. Skip this step. Do not gather, extract, or inject
architecture context. Re-enable when docs are curated.

<!-- DISABLED — was conditional injection of docs/architecture/{subsystem}.md
Before building batches, check whether architecture documentation is available
and mature enough to provide useful cross-file context. This step is dormant
for subsystems that have not yet been overhauled — it auto-activates as
`/overhaul {directory}` completes `doc-generate` for each subsystem.

#### 2.5a. Derive subsystem(s) from changed files

Use the same system/subsystem grouping logic from step 3c. For each unique
(system, subsystem) pair in the changeset, identify the subsystem directory:

| System | Subsystem path pattern |
|---|---|
| `stargate` | `services/universal-stargate/systems/{subsystem}/` or `services/universal-stargate/gateway_websocket/` etc. |
| `gateway` | `services/_universal-llm-gateway/src/{subsystem}/` |
| `rag` | `services/rag/` |
| `pipelines` | `pipelines/{pipeline_name}/` |
| `libs` | `libs/{lib_name}/` |

The subsystem name is the directory name used to look up
`docs/architecture/{subsystem_name}.md`.

#### 2.5b. Quality gate per subsystem

For each subsystem, check all three conditions:

```bash
ARCH_DOC="docs/architecture/{subsystem_name}.md"

# 1. File exists
test -f "$ARCH_DOC"

# 2. Contains GENERATED:START marker (produced by doc-generate, not hand-written)
grep -q 'GENERATED:START' "$ARCH_DOC"

# 3. File is >= 2KB (filters out stubs)
test "$(wc -c < "$ARCH_DOC")" -ge 2048
```

**If any condition fails**: log `"Architecture context skipped for {subsystem_name}: doc not yet overhauled"`
and proceed without context for that subsystem. The code review runs normally —
no degradation.

**If all three pass**: the subsystem is **activated** for architecture context injection.

#### 2.5c. Extract inventory for activated subsystems

For each activated subsystem, run the tree-sitter extraction via `doc_extraction`:

```bash
python3 -c "
import json
from pathlib import Path
from doc_extraction import extract_subsystem_inventory

workspace = Path('.')
result = extract_subsystem_inventory(Path('{SUBSYSTEM_DIR}'), workspace.resolve())
Path('/tmp/cr-extract-{SUBSYSTEM_NAME}.json').write_text(json.dumps(result, indent=2))
print(f'Extracted: {result[\"file_count\"]} files, {len(result[\"classes\"])} classes, {len(result[\"functions\"])} functions')
"
```

#### 2.5d. Condense inventory into review context

For each activated subsystem, build a condensed context block. The condensed
form includes module paths with docstring first lines, class/function signatures
with docstring first lines — not the full verbose JSON.

```bash
python3 -c "
import json
from pathlib import Path

inv = json.loads(Path('/tmp/cr-extract-{SUBSYSTEM_NAME}.json').read_text())
arch_doc = Path('docs/architecture/{SUBSYSTEM_NAME}.md').read_text()

lines = ['### Architecture context', '', arch_doc, '', '### Extraction inventory (signatures + docstrings)', '']
for mod in inv['modules']:
    doc_line = mod['docstring'].split('\n')[0][:120] if mod['docstring'] else '(no docstring)'
    lines.append(f'- **{mod[\"path\"]}**: {doc_line}')
for cls in inv['classes']:
    doc_line = cls['docstring'].split('\n')[0][:120] if cls['docstring'] else ''
    lines.append(f'  - class {cls[\"signature\"]}: {doc_line}')
    for m in cls.get('methods', []):
        if m['name'].startswith('_') and m['name'] != '__init__':
            continue
        doc_line = m['docstring'].split('\n')[0][:80] if m['docstring'] else ''
        lines.append(f'    - {m[\"signature\"]}: {doc_line}')
for fn in inv['functions']:
    doc_line = fn['docstring'].split('\n')[0][:120] if fn['docstring'] else ''
    lines.append(f'  - {fn[\"signature\"]}: {doc_line}')

Path('/tmp/cr-arch-context-{SUBSYSTEM_NAME}.md').write_text('\n'.join(lines))
print(f'Context: {len(lines)} lines, {sum(len(l) for l in lines)} chars')
"
```

Store per-subsystem context in `/tmp/cr-arch-context-{SUBSYSTEM_NAME}.md`.
These files are consumed by step 3e when building batch payloads.
-->

---

### 3. Run Code-Review Pipeline

#### 3a. Estimate size and SLOC

```bash
wc -c {FILE_1} {FILE_2} ... | awk 'NR>1 && $2!="total" {
  bytes=$1; tokens=int(bytes/3.5); sloc=int(bytes/45); file=$2
  printf "%8d bytes  %6d tokens  ~%4d SLOC  %s\n", bytes, tokens, sloc, file
}'
```

Output columns: `(bytes, estimated_tokens, estimated_SLOC, filename)`.

Heuristics: `bytes / 3.5 ≈ tokens`, `bytes / 45 ≈ SLOC` (typical Python/YAML).

#### 3b. Pre-flight classification (per file)

Budget constant: **12,000 source tokens per batch** (matches `budget_source_tokens` in `chain.yaml`).
SLOC limit: **500 lines** (relaxed from base `engineering-kernel.mdc` — cloud models handle up to 500 SLOC reliably).

For each file apply this logic in order:

| Condition | Classification | Action |
|---|---|---|
| `bytes > 42,000` (> 12k tokens) | **oversized** | Submit solo — flag for modularization in report |
| `bytes > 22,500` (≈ SLOC > 500) | **over-SLOC** | Submit solo — flag for modularization in report |
| `bytes ≤ 22,500` | **normal** | Include in grouping pool |

**Invariant**: ∀ over-SLOC ∨ oversized file: always solo, never grouped with other files.

Over-SLOC and oversized files are still reviewed. Flag them in the report with a
modularization recommendation, but do NOT skip them.

#### 3c. Nearest-parent grouping (cross-file context)

Changed files in a `/consult-review` run are usually clustered by local module
or package. Group by the **nearest shared parent directory**, but stop that
grouping at a bounded root so unrelated areas do not collapse into one batch.

**Grouping rules** (applied to **normal** files only):

1. **Bounded root**:
   - `services/<service>/...` → root is `services/<service>`
   - `libs/<lib>/...` → root is `libs/<lib>`
   - `pipelines/<pipeline>/...` → root is `pipelines/<pipeline>`
   - `tools/<tool>/...` → root is `tools/<tool>`
   - Everything else → root is the top-level directory (`scripts`, `config`, `docker`, etc.)

2. **Nearest shared parent**:
   - Start from each bounded root and treat it as the candidate batch parent
   - If all files under that root fit the batch budget, keep them together
   - If the batch is too large, split one directory level deeper and retry
   - Repeat until each batch fits, or a file becomes a solo batch

3. **Oversized and over-SLOC files**:
   - `bytes > 42,000` or `bytes > 22,500` remain solo regardless of parent grouping

**Rationale**: nearest-parent grouping keeps adjacent files together without the
old system-wide batches that can swamp the reviewer with loosely related context.

#### 3d. Report the submission plan

Print before submitting, e.g.:

```
Submitting 12 files in 7 submissions:
  [services/universal-stargate/systems/routing] engine.py (5.7k) + feasibility.py (3.7k) + types.py (3.1k) = 12.5k
  [services/universal-stargate/systems/pipeline] state.py (4.2k) + registry_wiring.py (2.6k) = 6.8k
  [services/_universal-llm-gateway/src/core] event_forwarder.py (4.2k) + messages.py (3.0k) = 7.2k
  [pipelines/code_review] chain.yaml (2.0k) + models.yaml (0.0k) = 2.0k
  [scripts] rebuild-mcp.sh (1.1k) + rag-status (0.8k) = 1.9k
  [solo, over-SLOC] lifecycle.py (8.0k) — modularize recommended
  [solo, oversized] federated_routing.py (15.9k) — modularize recommended
```

#### 3e. Submit sequentially

Submit each batch one at a time. Use deterministic file artifacts and avoid
fragile shell one-liners or heredocs.

```bash
# Kill orphan review processes from prior interrupted runs (safe no-op when none).
pkill -f "curl.*9999.*code-review\|python3.*code-review" 2>/dev/null || true
```

Write one newline-delimited file list containing the full review scope, then let
the helper derive nearest-parent groups and submit them sequentially. Architecture
context (step 2.5) is disabled — do not prepend architecture docs.

```bash
# Example full review scope
cat > "${SESSION_DIR}/review-files.txt" <<'EOF'
{FILE_A}
{FILE_B}
{FILE_C}
EOF

# Compute timeout from total source size (minimum 120s / 2 minutes)
TIMEOUT="$(python3 - <<'PY'
from pathlib import Path
files = [line.strip() for line in Path('${SESSION_DIR}/review-files.txt').read_text().splitlines() if line.strip()]
total_bytes = sum(Path(f).stat().st_size for f in files)
print(max(120, int((total_bytes / 3.5) / 100)))
PY
)"

# Deterministic submission with grouped payload+response artifacts
python3 scripts/consult_review_submit.py \
  --session-dir "${SESSION_DIR}" \
  --files-file "${SESSION_DIR}/review-files.txt" \
  --model "code-review" \
  --max-time "${TIMEOUT}" \
  --max-batch-bytes 42000 \
  --start-index 1
```

**Timeout scaling**: `timeout_seconds = max(120, total_batch_tokens / 100)` where
`total_batch_tokens` is from source files only. Minimum 2 minutes per batch.
Estimation: `bytes / 3.5 ≈ tokens`. This prevents timeouts on larger batches.

**Timeout handling**: if a grouped batch times out, retry that batch with a
smaller `--max-batch-bytes` or fall back to solo submission for its files.
Record the timeout and continue — do not abort the entire run.
After any timeout, report timeout-prone models before retrying:

```bash
scripts/report-timeout-models --pipeline code-review
```

If the same model appears repeatedly, avoid it by updating
`pipelines/code_review/models.yaml` to a healthy reviewer model (workspace
default: `google/gemini-2.5-flash`) unless the user explicitly requests
otherwise.

Merge all `${SESSION_DIR}/code-review-batch*.json` outputs into one severity-grouped report
once all submissions complete.

**Why helper-script submission**: it eliminates heredoc/quote drift, handles
nearest-parent batching consistently, and preserves deterministic payload and
response artifacts for every submission.

**If Stargate itself is not reachable** (connection refused on `:9999`), stop and tell the
user to start it via `./manage`. Do not attempt any fallback review path — there is no
equivalent substitute for the structured pipeline output.

## Operator Behavior Reference

| Scenario | Behavior |
|---|---|
| Batch fails mid-chain | Error recorded per phase; output includes completed phases + error reason |
| Timeout on large payload | Error message includes elapsed time and "timeout" label for actionability |

## Failure Classification

| Symptom | Classification | Next step |
|---|---|---|
| `code-review` absent from `/v1/models` | **Blocking** — pipeline filtered out | Stop. Check `pipeline.registry.unavailable` events; start cloud proxy via `./manage`; wait for auto-reload or restart Stargate |
| `cloud.proxy.unavailable` in events | **Blocking** — proxy health probe failed | Start cloud proxy via `./manage`; cloud models must appear in `/v1/models` before retrying |
| `MODEL_NOT_FOUND` from `/v1/chat/completions` | **Blocking** — pipeline not registered | Same as absent from `/v1/models` above |
| "RAG service not available (socket absent)" | Configuration — RAG not running | Run `./manage` or pass `--no-rag` |
| "timeout after Xs" in result | Transient — model overloaded | Run `scripts/report-timeout-models --pipeline code-review`, then retry with smaller batches or switch reviewer model |
| "connection error" to `:9999` | Transient — Stargate not reachable | Check `lsof -i:9999`, restart via `./manage` |
| HTTP 4xx from pipeline | Fatal — malformed payload | Check payload structure |

### 4. Present Findings

The pipeline returns structured JSON with findings already categorized and
validated. Present them grouped by severity:

```markdown
## Review: {commit description or "current changes"}

### Execution Status (required)
- **Critical auto-fixed now**:
  - {file}:{symbol_or_range} — {one-line fix summary}
- **Not auto-fixed (requires approval)**:
  - {file}:{symbol_or_range} — {why it was not applied}
- **No action needed**:
  - {file} — {brief confirmation}

### Critical (must fix before commit)
- **{target}** [{category}]: {observation}

  Current:
  ```python
  {current_code}
  ```

  Fixed:
  ```python
  {fixed_code}
  ```

  Validator: {validator_reasoning}

### Warning (should fix)
...

### Suggestion (consider)
...

### Docstring Quality (required when Python files changed)
- Surface docstring findings explicitly for each changed Python file:
  - missing docstring on module/class/public function
  - too-short or low-information docstring
  - name-echo first sentence that only repeats the symbol name
- For each finding, provide concrete replacement text that includes:
  - purpose and non-obvious behavior
  - key invariants or constraints
  - side effects (state mutation, event emission, I/O) when relevant
- Severity classification:
  - **Critical**: missing docstring on changed public API/class/module
  - **Warning**: present but too short/name-echo/thin for architecture extraction
  - **Suggestion**: optional clarity expansion beyond baseline quality

### Event Coverage
| Behavioral change | Signal | Role | Scope | Status |
|---|---|---|---|---|
| {behavioral_change} | `{signal}` | {coordination\|observation} | {node\|global} | {status} |

### Event Coverage Gaps → Suggestions (required)
- Every `Event Coverage` row with `Status=missing` MUST be repeated as a concrete
  suggestion in `### Suggestion (consider)` with:
  - `target`: exact file/symbol where signal should be emitted
  - `action`: add/update event constructor + emission point
  - explicit status label (same format as other findings)
- Prioritize high-value event classes when proposing missing signals:
  - decision/branch outcomes with explicit reason fields
  - retryable vs permanent failure boundaries
  - queue/backpressure lifecycle transitions (enter/wake/timeout/cancel)
  - spillover/fallback/handoff transitions between gateways
  - invariant guard-block boundaries (condition prevented action)
  - recovery/unblocked transitions after prior failure states
- Treat missing event signals as first-class suggestions, not informational notes.
- If a user later says **"apply suggestions"**, this includes these event-gap suggestions
  by default (unless rejected for invariant conflict).
- If the reviewer output omits a target, the agent MUST derive and propose a best-effort
  wiring target before reporting to the user:
  - `file`: exact path
  - `symbol`: function/method where emission belongs
  - `when`: precise control-flow moment (before/after branch, success/failure edge)
  - `factory`: event constructor to add/update
  - `payload`: required fields
  - `role`: `coordination` (if consumed by state machines/queues) or `observation` (default)
  - `scope`: `node` (if meaningful only at originating node) or `global` (default)
- Event suggestions without proposed wiring are incomplete and must not be reported
  as final.

### Event Suggestions Implementation Status (required)
- Always include this section whenever event suggestions exist.
- For each event suggestion, explicitly report one status:
  - `Implemented` — signal + target file/symbol were updated in this turn
  - `Not implemented` — include exact reason:
    - missing emission target
    - invariant conflict
    - pending user approval
    - non-atomic/ambiguous suggestion
- Never leave event suggestions in an implicit or unknown state.

### Event Implementation Validation (MANDATORY)
- When implementing any event suggestion, treat event contracts and in-code event
  references as authoritative inputs (not optional context).
- Validate **every** new or changed event emission before reporting "Implemented":
  1. **Signal contract**: signal name matches `^[a-z]+(\.[a-z]+){1,4}$` and the
     semantic stage (decision, transition, failure, recovery) matches usage.
  2. **Payload contract**: required fields from `docs/event-contracts.md` are
     present and populated with real runtime values (no placeholders).
  3. **Factory alignment**: use/update existing subsystem event factories and
     constructor patterns where available; do not invent ad-hoc payload shapes.
  4. **Emission boundary**: emission point is at the correct control-flow edge
     (before/after decision, retry boundary, permanent-failure boundary, wake/release).
  5. **Failure observability**: failure events and error logging coverage both
     exist (events are required, error logs are still required).
  6. **Classification**: `role` and `scope` values match the signal's purpose.
     Signals consumed by state machines, admission control, or queues MUST have
     `role="coordination"`. Signals meaningful only at the originating node
     MUST have `scope="node"`.
- If any validation check fails, mark as `Not implemented` with the failed check
  and required follow-up.

### Error Logging Coverage (required)
- **Priority**: Logging errors is the priority. Explicit error logging on failure paths and
  caught exceptions is required; event emission does not substitute for it.
- Keep event streams as the primary debugging evidence for flow/coordination issues.
- Require explicit error logging for failure paths and caught exceptions:
  - ∀ caught exceptions: log at `WARN`/`ERROR` (or re-raise) per `quality-gates.mdc`.
  - Missing error logs on real failure boundaries are review findings (at least Warning).
- Treat "events exist but errors are not logged" as incomplete observability, not acceptable coverage.
- **Log noise**: Keep log noise low even at debug level. Do not add INFO/DEBUG verbosity by default;
  reserve extra verbosity for when actively debugging a specific issue. Flag unnecessary or
  noisy log statements (e.g. routine-path INFO/DEBUG that would clutter logs) as review findings.

### No issues found in
- {clean_files entries}
```

**Hard requirement**: every issue MUST include an explicit status label:
- `Status: ✅ Applied now`
- `Status: ⏸️ Pending user approval`
- `Status: ℹ️ Suggested only (not applied)`
- `Status: ⛔ Rejected (violates {rule})` — suggestion conflicts with a project invariant

### 5. Validate Against Project Invariants (MANDATORY before applying)

**The review pipeline uses external models that lack full awareness of project
rules, architecture invariants, and workspace conventions.** Before applying
any suggestion — Critical, Warning, or Suggestion — the agent MUST validate
it against the loaded rules and invariants.

For each suggestion, check:

| Check | Sources |
|---|---|
| Python conventions | `python312.mdc` — modern syntax (`X \| Y` not `Union`), `@override`, type hints, `match/case`, `TaskGroup`) |
| Modularization | `engineering-kernel.mdc` — SLOC limits, SRP, naming (¬ `utils/helpers/common`), domain isolation |
| Exception handling | `quality-gates.mdc` — ∀ caught: log or re-raise, ¬ silent failure, ¬ `getattr` defaults for resources |
| Event contracts | `event-debugging_ws.mdc`, `docs/event-contracts.md` — signal format, required payload fields, lifecycle guarantees |
| Event implementation references | Existing subsystem event constructors/factories and nearby emission sites in touched files/modules — preserve established payload/schema patterns |
| Service lifecycle | `service-lifecycle_ws.mdc` — ¬ start/stop services, ¬ direct scripts |
| Patterns | `patterns_ws.mdc` — event/telemetry factories, async hygiene, passthrough conventions |
| Port semantics | `topology_ws.mdc` — `:9999` sole client-facing, `:9998` container-internal only |
| Code style | `core_ws.mdc` — comments "why" only, ¬ narrate, FOL for constraints |
| Ecosystem | `core_ws.mdc` — sole maintainers, break loudly, no backward compat shims |
| Routing | `routing_ws.mdc` — if suggestion touches routing/feasibility |
| Federation | `federation_ws.mdc` — if suggestion touches federation code |

**If a suggestion violates a rule or invariant:**

1. Do NOT apply it
2. Mark it with `Status: ⛔ Rejected (violates {rule})` in the report
3. Explain the conflict: which rule/invariant, why the suggestion breaks it
4. If the suggestion has merit but needs adaptation, propose a rule-compliant
   alternative in the report

**Examples of violations to catch:**
- Suggesting `Optional[X]` instead of `X | None` (violates `python312.mdc`)
- Adding `getattr(obj, attr, default)` for resource access (violates `quality-gates.mdc`)
- Silent `except` blocks that swallow errors (violates exception handling invariant)
- Adding backward compatibility shims (violates sole-maintainer ecosystem rule)
- Suggesting comments that narrate what code does (violates code style)
- Adding `Union[X, Y]` or `asyncio.gather()` (violates Python 3.12+ patterns)
- Restructuring that creates `utils.py` or `helpers.py` (violates naming rules)

### 6. Apply Validated Fixes

After invariant validation:

- For **Critical** issues that passed validation: fix immediately, then re-run quality gates
- For **Warning** issues that passed validation: present to user, apply if user agrees
- For **Suggestion** issues that passed validation: note them but do not apply
  without explicit user instruction
- If the user says **"apply suggestions"** (or equivalent), apply validated
  items according to **Follow-up Intent Aliases** above (default full sweep in this
  workspace unless user explicitly requests suggestion-only). Include event-coverage
  missing-signal suggestions.
- For **any** issue that failed validation: do not apply; report the conflict
- For **event suggestions** specifically: always report implemented vs not implemented
  per signal, and include reason when not implemented.
- Event wiring proposals are recommendation-only by default: do NOT implement event
  suggestions unless the user explicitly approves implementation.

### 7. Re-review (optional)

If Critical issues were fixed, run the pipeline again on the fixed files
to verify no new issues were introduced.

### 8. Closing Checklist (required)

**Every `/consult-review` session MUST end with this block** — no exceptions,
even if there were no issues or no fixes were applied.

```markdown
---
## Review Summary

### ✅ Applied
- [ ] {file}:{symbol_or_line} — {one-line description of fix applied}
- (none if no fixes were applied)

### ⏸️ Pending Approval
- [ ] {file}:{symbol_or_line} — {issue description and proposed fix in one line}
- (none if nothing is waiting)

### ⛔ Rejected (invariant violation)
- [ ] {file}:{symbol_or_line} — {suggestion} — violates {rule}: {brief explanation}
- (none if no suggestions were rejected)

### ℹ️ Suggestions (not applied, no approval sought)
- [ ] {file}:{symbol_or_line} — {suggestion description}
- (none if no suggestions)

### 🧭 Event Suggestions Status
- [ ] Implemented: `{signal}` (role={role}, scope={scope}) at {file}:{symbol_or_line}
- [ ] Not implemented: `{signal}` — {reason}
- (none if no event suggestions)
---
```

Rules for this section:
- Every finding from the review MUST appear in exactly one of the four lists
- Every event suggestion from `Event Coverage` MUST appear in `### 🧭 Event Suggestions Status`
  with `Implemented` or `Not implemented` (plus reason)
- "Rejected" items are final — do not apply even if the user says "apply all";
  surface the conflict and offer a rule-compliant alternative if one exists
- "Pending Approval" items stay pending until the user explicitly approves; do NOT
  apply them silently in a subsequent turn
- If user approves a pending item in a follow-up message, apply it and move it to
  Applied in the next closing checklist
- "Applied" entries must include the file path and the specific symbol or line range
  that was changed — not just the filename

## Rules

- ¬ apply ANY suggestion without first validating it against loaded workspace rules and invariants — pipeline models are external advisors, not authority on project conventions
- ¬ proceed if `code-review` is absent from `/v1/models` — **hard stop**, no fallback, no manual review
- ¬ substitute a manual code inspection when the pipeline is unavailable — it is not equivalent
- ¬ apply Warning or Suggestion fixes without user approval
- A user instruction to "apply suggestions" includes missing-event suggestions derived
  from `Event Coverage` rows with `Status=missing`
- Always explicitly tell the user whether suggested event signals were implemented;
  if not implemented, always provide reason per signal
- Always propose event wiring (file/symbol/when/factory/payload) for missing-signal
  suggestions; implementation requires explicit user approval
- Logging errors is the priority; require error-level logging on failure paths and caught
  exceptions. Keep log noise low (no default INFO/DEBUG verbosity; reserve for debugging a
  specific issue). Do not treat event emission as a substitute for error logs.
- ∀ event suggestions marked `Implemented`: run the Event Implementation Validation
  checklist and report pass/fail per signal; no unchecked event implementations
- ¬ skip source verification (step 1b) — compilation, import, and event-contract checks run before pipeline submission; broken files waste capacity and pollute findings
- ¬ include staged files in `/consult-review` scope; review only unstaged tracked + untracked files
- ¬ batch over-SLOC or oversized files with other files — submit them as solo batches
- ∀ over-SLOC/oversized files: flag with modularization recommendation in the report
- ¬ skip this command before a multi-subsystem commit
- ¬ review unrelated `./tmp/` output files from prior sessions — they are ephemeral scratch, not production code
- For follow-up **apply all findings** intent, only artifacts inside the active
  `${SESSION_DIR}` are authoritative review inputs:
  `./tmp/consult-review-sessions/{SESSION_ID}/consult-review*.json` and
  `./tmp/consult-review-sessions/{SESSION_ID}/code-review-batch-*.json`
- At the end of each run, explicitly print and persist the active session path
  so follow-up implementation steps read the correct artifact set
- ¬ use artifacts from multiple session directories in one apply pass unless the
  user explicitly asks for a cross-session merge
- `/consult-review` implementation artifacts must live in a unique per-run
  directory under `./tmp/consult-review-sessions/`
- Session artifacts are read-only review inputs; do not treat them as code targets
- ¬ use inline `-d "$(python3 ...)"` for payloads — write to `${SESSION_DIR}/cr-batch-N-payload.json` and use `-d @file`
- 12,000 source tokens is the per-batch budget constant (encoded in `chain.yaml` as `budget_source_tokens`); estimation heuristic: `bytes / 3.5`
- SLOC estimation heuristic: `bytes / 45` — no extra grep needed; over-SLOC threshold at `bytes > 22,500`
- Group normal files by bounded nearest parent, not by whole-system batches
- If a parent group exceeds budget, split one directory level deeper until it fits or becomes solo
- Submit groups sequentially — one at a time — to avoid overloading the pipeline server
- Per-request timeout scaled to batch size: `max(120, total_batch_tokens / 100)` (minimum 2 minutes); on timeout, retry with smaller parent groups or solo files
- Architecture context (step 2.5) is disabled — docs are outdated and not maintained
- Always explicitly list any over-SLOC/oversized files and the modularization recommendation
- Always separate report sections into:
  1) `Fixed Immediately (Applied)`
  2) `Requires Approval (Not Applied)`
  3) `Suggestions (Not Applied)`
- If Python files are in scope, always include explicit docstring quality findings
  using overhaul standards (audience-aware, substantive, non-placeholder text)

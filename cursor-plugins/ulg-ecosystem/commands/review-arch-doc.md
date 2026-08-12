# /review-arch-doc

Review a generated architecture document (the kind produced by the
`doc-generate` pipeline at `/overhaul` step 9) against the source directory
it describes, the extraction inventory it was generated from, and workspace
invariants.

This command is a **task adapter** for the architecture-handoff-protocol. The
shared rules govern the packet contract, dispatcher behavior, validation, and
closure:

- `architecture-handoff-protocol.mdc` — the six required packet blocks,
  validation contract, iteration & closure
- `projects/.cursor/rules/handoff-dispatchers.mdc` (workspaces sandbox) —
  capability matrix, dispatch shape, polling, failure handling
- `agent-skills/consult-routing.md` + `universal-llm-gateway/tmp/reviews/_handoff-packet-template.md`
  — transport axis and packet skeleton

`/review-arch-doc` provides only the task adapter: doc/source/extraction
discovery, the 10 doc-soundness questions, corpus assembly, doc-marker
semantics (GENERATED / AUTHORED / HUMAN), and the `Section:` + `Question:`
location refinement.

`/review-arch-doc` is intended to replace the advisory `consult-frontier`
panel at `/overhaul` step 11 when MCP-equipped review is available.

## When to Use

- After `/overhaul` step 10 manual review, before committing the doc at step 12
- When iterating on an architecture doc after applying first-pass review findings
- Whenever a generated doc needs a hard accuracy + coverage check that goes
  deeper than `consult-frontier`'s panel

## Invocation

```
/review-arch-doc [mode] <doc-path> [extraction-json] [--source <dir>]
```

`mode` — optional dispatcher name. Default: **CDP-native**
(`team_dispatch(model=cdp/opus-5)`; `project_ask` = escape only). Valid modes per
`projects/.cursor/rules/handoff-dispatchers.mdc`: `cdp` (default), `web-claude` (legacy fallback after CDP-down operator choice), `team-generate [model]`, plus reserved
stubs (raw frontier, `team:*`) that stop with a guidance message because
arch-doc review needs live source-file access.

`doc-path` — required path to the generated architecture markdown (typically
`docs/architecture/<subsystem>.md` or `<doc-path>.generated`).

`extraction-json` — optional path to the doc-generate result JSON (typically
`/tmp/doc-generate-result.json`). Provides `unsupported_claims`,
`missing_coverage`, `human_markers` as ground truth for the reviewer.

`--source <dir>` — optional source directory the doc describes. If omitted,
attempt to infer from the doc filename (`docs/architecture/foo.md` →
`services/foo/` or `libs/foo/` if either exists).

| Argument | Effect |
|---|---|
| omitted or `cdp` | **CDP-native** dispatcher (default) — `team_dispatch(model=cdp/opus-5)` bus-nudge |
| `web-claude` | Legacy handoff + manual operator push (fallback only) |
| `team-generate` | `team-generate` dispatcher; model `openai/gpt-5.4` |
| `team-generate gemini` | `team-generate` dispatcher; model `google/gemini-3-pro-preview` |
| `team-generate <full-model-id>` | `team-generate` dispatcher; model as supplied |
| `openai` / `gemini` / `team:*` / `<full-model-id>` | reserved stub — stops with guidance |

Examples:

```
/review-arch-doc docs/architecture/proxy.md
/review-arch-doc docs/architecture/proxy.md /tmp/doc-generate-result.json
/review-arch-doc web-claude docs/architecture/scheduling.md \
    /tmp/doc-generate-result.json --source services/universal-stargate/src/scheduling
/review-arch-doc team-generate docs/architecture/proxy.md /tmp/doc-generate-result.json
/review-arch-doc team-generate gemini docs/architecture/proxy.md
```

## Instructions

### 0. Resolve Dispatcher and Paths

Parse args. Map `cdp` (default), `web-claude`, and `team-generate [model]` per the table above.
Reserved stubs stop with: "Raw frontier dispatch (no MCP tools) is reserved
for /review-arch-doc. Arch-doc review needs live source-file access. Re-run
with `team-generate`, `cdp`, or `web-claude`."

Resolve `DOC_PATH` (.md), `EXTRACTION_PATH` (.json, optional), `SOURCE_DIR`
(--source or inferred from doc basename: `services/<name>/` or `libs/<name>/`).

Stop with guidance if `DOC_PATH` is missing, or if `SOURCE_DIR` cannot be
inferred and no `--source` was supplied.

### 1. Sanity-Check Inputs

```bash
[ -f "$DOC_PATH" ] || stop "doc not found: $DOC_PATH"
[ -d "$SOURCE_DIR" ] || stop "source dir not found: $SOURCE_DIR"
DOC_LINES=$(wc -l < "$DOC_PATH")
SOURCE_PY_COUNT=$(find "$SOURCE_DIR" -name '*.py' -not -path '*/__pycache__/*' | wc -l)
SOURCE_PY_LOC=$(find "$SOURCE_DIR" -name '*.py' -not -path '*/__pycache__/*' \
                  -exec wc -l {} + | tail -1 | awk '{print $1}')

if [ -n "$EXTRACTION_PATH" ]; then
    [ -f "$EXTRACTION_PATH" ] || stop "extraction json not found: $EXTRACTION_PATH"
    UNSUPPORTED=$(jq -r '.unsupported_claims // [] | length' "$EXTRACTION_PATH")
    MISSING=$(jq -r '.missing_coverage // [] | length' "$EXTRACTION_PATH")
    HUMAN_MARKERS=$(jq -r '.human_markers // [] | length' "$EXTRACTION_PATH")
fi
```

Report: doc path + line count, source dir + file count + total LOC, extraction
inventory counts (if provided).

**Stop** if any required input is missing or empty.

**If `SOURCE_PY_LOC` > 8000**: warn and offer to scope source corpus down to
the most-changed files or a subdirectory. The reviewer can still read the rest
via MCP `fs` reads.

### 2. Build the Packet

Materialize the six required blocks per `architecture-handoff-protocol.mdc`.

#### `<scope>` (task adapter)

```
Doc: <doc-path>, <line-count> lines
Source: <source-dir>, <file-count> files, <total LOC>
Extraction: <extraction-path or "none">
  - unsupported claims: <N>
  - missing coverage: <N>
  - human markers: <N>
```

#### `<invariants>` (shared protocol template)

Extract per `architecture-handoff-protocol.mdc` § "Block 2": compose universal
parent-rule invariants (shared `libs/` primitives, transport, ModelId, events,
MCP relay, quality/scope) with loaded workspace `_ws.mdc` invariants. For
arch-doc review, prioritize categories the doc is most likely to surface or
violate: transport, events, API namespace, service lifecycle, topology port
semantics, MCP relay, and workspace-critical patterns.

**Skill delivery (CDP / web-anthropic — fleet rule):** Use the
`claude-ai-cdp-navigation` skill § Skill delivery — fleet rule.

- **Inline** (not Claude slugs): `architecture-invariants` + `ulg-architecture`
  tag floors the doc must obey. Optional short `prose-discipline` excerpt when
  editorial findings are in scope. **¬ slash** these. **¬** cite retired
  `cortex://agent-skills/*.md`.
- **Claude-slug engage** (`/` or `Use the … skill`): `evidence-review-discipline`,
  `reasoning-posture`, `no-silent-inference`.
- **Fail closed** before CDP submit if required inlines are missing.

For `team-generate` dispatch, Claude-slug engage is optional — the protocol's
default invariants composition is sufficient since frontier loads workspace rules
via its own MCP path. Still **inline** architecture-invariants + ulg-architecture
excerpts into the packet for audit-trail parity across modes.

#### `<task_guidance>` (task adapter — the 10 doc-soundness questions)

```
<task_guidance>
For each section of the doc, evaluate against these 10 doc-soundness
questions. Findings should reference the question number for traceability
(e.g. "Section 'Routing' — Q2: missing CapacityPool…").

1. ACCURACY (CLAIM-LEVEL)
   Does each factual claim trace to actual source? Spot-check assertions
   about behavior, lifecycle, invariants, signal emissions, and inter-module
   contracts against .py files. Flag claims that are wrong, stale, or
   over-stated.

2. COVERAGE
   Are major responsibilities, public interfaces, and key invariants of the
   subsystem covered? For each significant module/class in the source list,
   is there a doc section that mentions it?

3. ABSTRACTION SOUNDNESS
   Does the abstraction the doc describes hold up under examination? Are
   boundaries clean? Does the doc paper over a leaky abstraction or layering
   inversion?

4. FAILURE MODES
   Are the subsystem's failure modes and recovery behavior documented?
   Retries, partial failures, timeouts, eviction, fallbacks, circuit-breaker
   conditions?

5. INVARIANT CALL-OUTS
   Does the doc surface the workspace invariants this subsystem MUST obey
   (UDS transport, signal format, API namespace, etc.)? Quote the relevant
   doc text or note its absence.

6. EXTENSION POINTS
   Are extension points described honestly (vs aspirationally)? If the doc
   says "to add a new X, do Y", does the code actually support that?

7. DEAD / DEPRECATED REFERENCES
   Does the doc describe code that no longer exists, classes that were
   renamed, or paths that were moved? Cross-reference each named symbol
   against source.

8. CROSS-REFERENCE INTEGRITY
   Are referenced files, line numbers, signal names, config keys correct?
   Names matter: `signal.foo.bar` vs `signal.foo.baz` differs for retrieval
   and operator runbooks.

9. EXTRACTION INVENTORY HONESTY
   If an extraction inventory is provided (`unsupported_claims`,
   `missing_coverage`, `human_markers`), does the doc resolve each item, or
   leave gaps unaddressed? Flag remaining unresolved items.

10. RAG QUALITY
    Will the doc produce good retrieval results for plausible operator and
    developer questions about this subsystem? Are distinctive terms used? Is
    the language specific enough to differentiate from other docs?

A clean doc passes all ten.

Respect doc structural markers:
- Sections wrapped by <!-- GENERATED:START --> ... <!-- GENERATED:END -->
  came from doc-generate; flag accuracy issues, do NOT flag style.
- Sections containing <!-- AUTHORED --> were hand-written; check accuracy
  and coverage but allow stylistic variation.
- <!-- HUMAN: ... --> markers are explicit deferrals — flag them ONLY if
  unresolved when they should be by now.
</task_guidance>
```

#### `<corpus>` (task adapter — doc + extraction + curated source)

```bash
PY_FILES=$(find "$SOURCE_DIR" -name '*.py' -not -path '*/__pycache__/*' | sort)

{
    echo "=== DOC: $DOC_PATH ($DOC_LINES lines) ==="
    cat "$DOC_PATH"

    if [ -n "$EXTRACTION_PATH" ]; then
        echo ""
        echo "=== EXTRACTION INVENTORY: $EXTRACTION_PATH ==="
        jq '{unsupported_claims, missing_coverage, human_markers}' "$EXTRACTION_PATH"
    fi

    echo ""
    echo "=== SOURCE FILES (count: $SOURCE_PY_COUNT, total LOC: $SOURCE_PY_LOC) ==="
    for f in $PY_FILES; do
        lines=$(wc -l < "$f")
        echo ""
        echo "--- $f ($lines lines) ---"
        cat "$f"
    done
}
```

If `SOURCE_PY_LOC` > 8000 and the user opted to scope down: include only the
listed subset; note in `<scope>`: `Source corpus scoped to: <list>`. The
reviewer can fetch the rest via `fs` reads.

#### `<mcp_capabilities>` (shared protocol template)

Use the dispatcher-flavored block from `projects/.cursor/rules/handoff-dispatchers.mdc` for the
chosen dispatcher.

#### `<output_format>` (shared template, task-flavored)

v1 structured shape per `architecture-handoff-protocol.mdc` § "Block 6:
`<output_format>`", with the arch-doc adapter refinement: use `Section:`
(doc-section-heading | cross-doc | invariant:<name>) instead of `File:`
when the corpus is a single document, and include the optional `Question:`
field referencing Q1..Q10 or invariant:<name>.

```
FindingID:    F<n>                              # stable, sequential
Severity:     Critical | Warning | Suggestion
Section:      <doc-section-heading> | cross-doc | invariant:<name>
Question:     Q1..Q10 | invariant:<name>       # optional
FileReadVia:  fs | absolute | not_read
Concern:      <one paragraph; cite invariant tag if applicable>
Evidence:     <MCP tool calls and what they returned>   # required if dispatcher has MCP
Operation:    replace | create_file | delete_file | delete_substring |
              replace_whole_file | replace_all_occurrences |
              plan_required | needs_info | deferred | blocked
DependsOn:    F<m>[resolved | applied | approach=A]   # semantic only
```

For mechanical Operations: include an Edits block (column-0 SEARCH/REPLACE
fences, Occurrence=exactly_once, ApplyAfter) and a Verify list (commands
from the closed Verify Allowlist per protocol § "v1 Dispatcher Apply
Contract"). For paused Operations: include required subfields per protocol
§ "Paused operations" (plan_required → Scope/MinimalPlan/AcceptanceChecks;
needs_info → WouldFetch or Questions; deferred → Options ≥2;
blocked → BlockedReason + UnblockedBy).

Reviewer MUST NOT emit: NewlineMode, FileSha256Before, ExpectedCount.

Return ONLY findings. If nothing found, return exactly: "No findings."

### 3. Dispatch

Per `projects/.cursor/rules/handoff-dispatchers.mdc`. Packet materialized once; dispatcher adapts
transport.

**CDP-native (default):**

1. Write six-block packet to `tmp/reviews/<doc-slug>-cdp-archdoc-packet.md`
   (skill delivery per § `<invariants>` — inline non-slugs; engage Claude slugs)
2. **Answer-3 preflight:** stage corpus + prompt to `cortex://notes/system/threads/{arc-slug}/…` with source coverage for **every area the doc asserts**; list omitted paths explicitly; **reject dispatch** if preflight incomplete **or** required skill inlines missing (Use the `claude-ai-cdp-navigation` skill)
3. **Required** ≤25-line bus pointer on arc coordination thread (URI table only — omittable only for one-shot non-arc asks)
4. `team_dispatch(op=generate, model=cdp/opus-5, contract=light-bounded, sidecar_ref=cortex://…, dispatch_thread_id=…)` — wait via `poll_hint` / `agent_bus.wait` until `archive_uri`. **Escape only:** `project_ask(op=submit, …)` + MCP poll when `team_dispatch` CDP unavailable
5. **Submit before turn close** — suppress push reminder when CDP prompts the same thread (a25444)
6. Triage harvest per validation contract below

For `web-claude` (legacy fallback): write packet to
`tmp/reviews/<doc-slug>-claude-web-archdoc-packet.md`, summary to
`tmp/reviews/<doc-slug>-claude-web-archdoc-summary.md`. Post via `agent_bus`
with subject `Arch-doc review — <doc-name>`, tags `project:<repo>`,
`type:review`, `agent:claude-web`, `doc:<doc-slug>`. Manual operator push required.

For `team-generate`: dispatch via
`team_dispatch(op="generate", role="reviewer", dispatch_thread_id=…, max_tool_turns=25, …)`.
Poll via `pipeline(op="result", execution_id=EXEC_ID, wait_seconds=60)`.

### 4. Validate, Triage, Iterate

Per `architecture-handoff-protocol.mdc` § "Validation Contract" and § "Iteration".

Five-bucket triage: Apply / Reject by rule / Escalate / Surface for triage / Defer.

Doc-specific note for triage: a finding that contradicts a `<!-- GENERATED -->`
section is more weighty than one against an `<!-- AUTHORED -->` section,
because GENERATED came from extraction and should be ground-truth.

On iteration: stale corpus — instruct reviewer to re-read live doc + source
via `fs(sandbox="workspaces", op="read", ...)` for MCP-equipped dispatchers;
rebuild packet with `<prior_pass>` block for non-MCP dispatchers.

### 5. Close and Artifact

Per `architecture-handoff-protocol.mdc` § "Closure" and § "Artifact".

Write the summary artifact at
`tmp/reviews/<doc-slug>-<dispatcher>-archdoc-summary.md` with the standard
sections (Summary, Critical/Warnings/Suggestions, Applied, Rejected by Rules,
Surfaced for Triage, Iteration History).

Source-of-truth per `projects/.cursor/rules/handoff-dispatchers.mdc`: CDP harvest sidecar for
default mode; agent_bus thread for legacy `web-claude`; iteration history (with `execution_id` per pass) for `team-generate`.

## Arch-Doc-Review Specifics

These are the only `/review-arch-doc`-specific deviations from the protocol;
all else is shared.

- **Source-dir inference** (step 0) — fall back to `services/<name>/` or
  `libs/<name>/` from doc basename when `--source` not supplied.
- **Extraction inventory honesty** (Q9) — when provided, the inventory's
  unresolved items are first-class findings.
- **Doc structural markers** — GENERATED / AUTHORED / HUMAN markers gate
  what kinds of findings are appropriate per section.
- **8000-LOC source warning gate** (step 1) — large subsystems can be scoped
  down; the MCP-equipped reviewer reads the rest via `fs`.
- **`Section:` + `Question:` location refinement** — supersedes the protocol's
  default `Location:` field.
- **Reserved stub modes** — raw frontier (no MCP tools) is reserved because
  arch-doc review needs live source-file access.

## Rules

- ¬ proceed if any required input (doc, source dir) is missing
- ¬ proceed past 8000 source LOC without user confirmation (offer scope-down)
- ¬ flag style issues against `<!-- GENERATED -->` sections
- ¬ skip the validation contract (`architecture-handoff-protocol.mdc`)
- ¬ run reserved stub modes — they stop with guidance
- All other rules inherited from `architecture-handoff-protocol.mdc` and
  `projects/.cursor/rules/handoff-dispatchers.mdc`

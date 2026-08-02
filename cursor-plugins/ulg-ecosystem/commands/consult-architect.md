Architectural validation of a draft spec. Reads the spec and key
implementation files, validates schemas and integration points against
actual code, discovers relevant RAG scopes, then consults cloud models
to surface gaps before creating implementation phases.

## Usage

```
/consult-architect {spec_path}
```

Where `{spec_path}` is relative to the project root (e.g.,
`tasks/specs/rag-metadata-consolidation.md`).

## When to Use

- A draft spec (`cortex://notes/system/specs/*.md`) exists and needs architectural validation
- The spec proposes schema changes, new tables, or data model modifications
- The spec crosses subsystem boundaries (e.g., RAG service + MCP server +
  pipeline handlers)
- You want to verify integration points and data compatibility against actual
  code before committing to a phase structure

Do NOT use for planning or implementation — use `/consult-plan` for that.
This command validates the *spec*; `/consult-plan` creates the *plan*.

**Workflow position**: draft spec → `/consult-architect` → incorporate feedback
→ create phases → `/consult-plan --review` (optional) → implement

## Instructions

### 1. Read and Understand the Spec

Read `{spec_path}` fully. Identify:
- The Problem and Objectives sections
- The proposed Data Model / schema changes
- The "Key files" table (implementation files the spec references)
- The "Unidentified needs" section (if present)

### 2. Read Key Implementation Files

Read every file listed in the spec's "Key files" table. If the spec doesn't
have one, identify the 5-10 most relevant source files from the spec's
description of what changes.

Keep to ≤10 files. If more are referenced, prioritise by how central they
are to the proposed changes.

### 3. Validate Spec Against Actual Code

This is the agent's own analysis — before consulting external models:

- **Schema compatibility**: do proposed table schemas match the data
  structures in current code? Are there fields in the code not captured
  in the spec?
- **Integration points**: are all consumers of the data being changed
  identified? Check for direct file reads, imports, config references.
- **Data flow**: trace how data flows from producers to consumers. Are
  there hops the spec missed (e.g., pipeline handlers loading YAML
  directly, hardcoded paths, container volume mounts)?
- **Missing files**: are there files that interact with the proposed
  changes but aren't listed in the spec?

Document findings as bullet points — these feed into the consultation prompt
and spec update.

### 4. Run Consultation

Derive a slug from the spec filename (e.g., `rag-metadata-consolidation`).

```bash
source ~/.venvs/universal/bin/activate
cat <<'EOF' | python scripts/consult -r architect \
  -Q - \
  -f {spec_path} \
  -f {file1} -f {file2} ... \
  --models openai/o3 \
  --no-rag \
  -o ./tmp/consult-{spec-slug}.md
Validate this spec against the attached source files. Identify:
1. Schema mismatches between proposed tables and current data structures
2. Missing integration points (consumers not listed in the spec)
3. Data flow gaps (hops, volume mounts, config references the spec missed)
4. Rollback risks and migration ordering concerns
5. Event vocabulary impact (new/modified/deprecated signals)

For each gap found, provide the concrete fix — not just a description.
EOF
```

Use one `-f` flag per file from step 2. Use `--no-rag` — architecture docs
are not reliably indexed for RAG retrieval; the source files attached via
`-f` are the ground truth.

Use `--models` with an explicit frontier model — `--cloud-only` does not
work on the pipeline path (the pipeline's `source: cloud` model_requirements
should handle it, but auto-selection can fall back to local models).

#### Model selection for architect role

Architecture validation is reasoning-heavy. Prefer models with strong
reasoning and large context:

| Model ID | Routing | Notes |
|---|---|---|
| `openai/o3` | OpenRouter | Default — deepest reasoning |
| `native/anthropic/claude-sonnet-4` | Direct Anthropic API | Good reasoning, cheaper than `anthropic/` on OpenRouter |
| `google/gemini-2.5-pro` | OpenRouter | Large context, competitive pricing |

Use `native/anthropic/` prefix (direct API) over `anthropic/` prefix
(OpenRouter) for Anthropic models — better pricing.

Important: do not pass `{spec_path}` via `-Q` when also providing a prompt.
In this repo's `scripts/consult`, `-Q` is the question source; the spec itself
must be attached with `-f {spec_path}`.

### 5. Synthesize

Read `./tmp/consult-{spec-slug}.md`. Combine the consultation output with
your own analysis from step 3:

1. Identify consensus across models — where do they agree?
2. Note disagreements and evaluate which position is correct given the
   actual code you read
3. Validate every suggestion against workspace rules and project invariants
   (consultation models lack rule awareness)
4. Produce a unified findings list: what the spec got right, what it missed,
   what needs changing

### 6. Update the Spec

Apply findings to `{spec_path}`:

- Resolve items in the "Unidentified needs" section
- Add a new "Integration points (discovered during validation)" subsection
  if the consultation found consumers or data paths the spec missed
- Fix schema issues (missing fields, type mismatches, missing indexes)
- Append to the "Consultation" section with provenance:

```markdown
## Consultation

**Source**: `/consult-architect` run on {date}
**Models**: {list from consult output}

### Key findings
- {finding 1}
- {finding 2}
...
```

Present the updated spec to the user. Ask: "Spec updated with architectural
validation. Proceed with `/consult-plan` or `/create-implementation-plan`?"

## Rules

- ¬ use this for planning or implementation — validate the spec only
- ¬ include more than 10 `-f` files — keep context focused
- ¬ skip the agent's own validation (step 3) — the consultation is a second
  opinion, not a replacement for reading the code yourself
- ¬ apply consultation suggestions that violate project invariants — reject
  with explanation (see `consultation-workflow_ws.mdc` § Invariant Validation)
- Output goes to `./tmp/consult-{spec-slug}.md` (ephemeral, not committed)
- The spec file itself IS updated (it's a durable artifact in `cortex://notes/system/specs/`)
- Use `--models` with an explicit frontier model — ¬rely on `--cloud-only`
  or pipeline auto-selection for the architect role
- Use `--no-rag` — architecture docs RAG is not reliably indexed

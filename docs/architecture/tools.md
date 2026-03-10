# Tools and Scripts

CLI tools for pipeline testing, viewing, model management, and operations.

## Pipeline Test (`tools/pipeline_test/`)

Snapshot pipeline executions, inspect step I/O, replay individual model calls,
compare outputs, and consult other models for improvements.

**Entry**: `python -m tools.pipeline_test <subcommand>`

| Subcommand | Purpose |
|---|---|
| `list` | List pipeline executions |
| `snapshot` | Save execution to self-contained fixture |
| `inspect` | View full execution metadata |
| `refine-context` | Progressive step inspection (summary → prompt → output) |
| `replay` | Re-run a step with modified prompts/models/params |
| `compare` | Unified diff between original and replay |
| `consult` | Query other models for prompt improvement suggestions |
| `ingest-papers` | Index PDFs into RAG corpus |
| `measure-profile` | Measure RAG retrieval profiles |

### Snapshot Source

Pipeline events from: `/tmp/logs/universal-stargate/pipeline_summaries/{pipeline_id}/{timestamp}_{exec_id}/events.jsonl`

Fixtures saved to `tools/pipeline_test/fixtures/` (gitignored, self-contained).

### Consult

Queries consultant models for prompt improvement suggestions. Default is **chained** (sequential: each model reviews the prior model's output); use `--parallel` for independent runs.
Consult delegates to `scripts/consult_lib/core.py` (`execute_consult`) with role
`prompt_engineer`, so RAG retrieval and model selection behavior stays consistent
with `scripts/consult`.
RAG scope is auto-detected from step model tier (cloud -> `llm_prompting`, local ->
`small_llm_prompting`) unless `--scope` is set explicitly.

## Pipeline Viewer (`tools/pipeline-viewer/`)

Web UI for viewing pipeline execution progress and results.

**Run**: `python tools/pipeline-viewer/server.py --port 8080`

| Endpoint | Purpose |
|---|---|
| `GET /api/executions` | List executions |
| `GET /api/executions/{pipeline_id}/{exec_id}` | Aggregated execution |
| `GET /api/executions/{pipeline_id}/{exec_id}/stream` | SSE for live runs |
| `GET /api/snapshots/{request_id}` | Request/response snapshots |

Data source: same pipeline event JSONL as pipeline_test.

## Cloud Proxy Browser (`services/universal_cloud_proxy/`)

Cloud proxy serves a model-pricing browser UI and cost-oracle endpoints.
This lives in the existing cloud proxy HTTP service (port 8200, loopback-only).

| Endpoint | Purpose |
|---|---|
| `GET /` | Browser UI for cloud model pricing |
| `GET /api/models` | Full OpenRouter catalog with per-million-token pricing and capability tags |
| `POST /api/select` | Task-aware model selection (filter by tags, cost, context, modality) |
| `POST /api/refresh` | Force browser catalog refresh |
| `GET /api/models/{model_id}` | Single-model pricing lookup |
| `GET /catalog/pricing` | Configured/filtered catalog with pricing for routing |

`/api/select` is used by `scripts/consult` for role-driven model auto-selection.
Data source split: browser endpoints use full public OpenRouter catalog; routing
endpoints use the configured provider-filtered subset.

### `/api/select` — tagging and ranking

Tags are derived from model ID patterns (conservative — only what the ID signals unambiguously):

| Tag | Triggered by |
|---|---|
| `code` | `code`, `coder`, `codex` in model ID |
| `fast` | `flash`, `lite`, `mini`, `fast` in model ID |
| `reasoning` | `thinking`, `:thinking`, `deepseek-r1`, `openai/o[134]` in model ID |
| `pro` | `-pro` at word boundary in model ID |
| `chat` | `chat` in model ID |
| `general` | fallback — only when no other tag matched |
| `vision/audio/video` | from OpenRouter modality field |
| `local` | all local Stargate models (source metadata, not ID pattern) |

Multimodal tags indicate additional capability, not reduced text quality.
They are **included by default** — exclude explicitly via `exclude_tags` if needed.

Tag filtering requires at least one match (OR semantics, not AND).
Models are ranked by quality tier (highest first, randomised within tier):

| Tier | Completion cost | Notes |
|---|---|---|
| 0 | free | Excluded in practice |
| 1 | < $0.50/M | Budget |
| 2 | $0.50–$5/M | Mid; local GPU models always here; `flash`/`haiku` capped here |
| 3 | $5–$30/M | High quality sweet spot |
| 2 (capped) | ≥ $30/M | Ultra-premium capped — diminishing returns |

Name boosts: `sonnet`, `gpt-4o` (non-mini) → tier 3 regardless of cost.
Name caps: `flash`, `lite`, `mini`, `haiku` → max tier 2.

## scripts/consult

Multi-model consultation with role templates, file injection, and optional RAG.
Source: `scripts/consult`. Roles: `scripts/consult-roles.yaml`.

Queries models in parallel (default) or chained sequentially (`--chain`).
Strips `<think>` blocks before chaining so reasoning tokens don't contaminate
the next model's context.

```bash
# In this workspace (RAG available)
consult "How should we handle WebSocket reconnection?"
consult -r architect "Should routing use events?" -f docs/event-contracts.md
consult -r reviewer -f systems/routing/router.py "Check for races"
consult -r planner "Add per-model timeouts" -o plan.md
consult --chain -r planner --models local cloud "complex question"
consult --no-rag "question"   # skip RAG, useful for quick queries
```

| Flag | Default | Purpose |
|---|---|---|
| `-r / --role` | `researcher` | See role table below |
| `--models` | Auto-selected per role (intelligence profiles -> `/api/select` fallback) | Model IDs to query |
| `--cloud-only` | off | Restrict to cloud IDs (`/`) and validate availability in live `/v1/models`; fail fast if cloud is unavailable |
| `-f PATH` | — | Inject file as context; repeat per file; directories glob `*.py` only |
| `--no-rag` | — | Disable RAG — required when calling from foreign workspaces |
| `--chain` | — | Sequential: first model analyses, rest review prior output |
| `--scope` | `project` | RAG retrieval scope: `project`, `research`, `all`. Multiple scopes: pass space-separated (e.g. `--scope project research`); RAG returns the union of those scopes' source_prefixes. |
| `--rag-pipeline` | — | Use `rag-context` pipeline for intelligent RAG retrieval |
| `-o PATH` | — | Save markdown output to file |
| `--timeout N` | 300s | Per-model timeout |

### Roles

Default role is `researcher` — note caveats below before using defaults.

| Role | System prompt focus | Works without RAG | Language-agnostic |
|---|---|---|---|
| `reviewer` | Correctness, races, leaks, severity classification | ✓ | ✓ |
| `architect` | Trade-offs, alternatives, Assessment→Recommendation | ✓ | ✓ |
| `planner` | Step-by-step plan with literal copy-paste code | ✓ | ✗ Python-only |
| `researcher` | Research-grounded Q&A, LLM systems domain | ✗ degrades | ✓ |
| `modularizer` | SLOC/SRP analysis, SIMPLIFY→SPLIT plan | ✓ | ✗ Python-only |
| `prompt_engineer` | Diagnose step failures, propose minimal prompt fixes | ✓ | ✓ |

**`researcher` (default)**: system prompt tells the model research papers are
provided — without RAG this framing is empty and the LLM-systems domain
assumption is wrong for non-gateway questions. Always pass `-r` explicitly.

**`planner` and `modularizer`**: hardcode Python 3.12+ conventions as MANDATORY.
Not suitable for JavaScript/TypeScript workspaces.

### Auto-selection (role → `/api/select` criteria)

When `--models` is omitted and `CLOUD_PROXY_URL` is set, the script queries
`/api/select` with role-derived criteria. Filter is OR semantics on tags.

| Role | Required tags (≥1) | Excluded tags | Min context |
|---|---|---|---|
| `reviewer` | code, reasoning | — | 32 768 |
| `architect` | general, reasoning | fast, local | 128 000 |
| `planner` | code, reasoning | local | 65 536 |
| `researcher` | general, reasoning | fast | 128 000 |
| `modularizer` | code, reasoning | — | 32 768 |
| `prompt_engineer` | code, reasoning | — | 32 768 |

**Local model exclusion**: `architect` and `planner` exclude local models by
default — local GPU models hallucinate file paths for roles that require codebase
knowledge. Pass `--models` explicitly to override; a warning is printed to stderr
when a local model is manually selected for these roles.

**Consequence**: `reviewer` auto-select returns code-specialist and
reasoning/thinking models. Claude Sonnet and Gemini Pro carry the `general`
fallback tag and are excluded. They appear for `architect`/`researcher`.
For a general-purpose code review using those models, pass `--models` explicitly.

RAG `/search` accepts `scope` as a single name or a list of names (union of scopes). Scope maps to `source_prefixes` via config; e.g. `project`: `docs/architecture`, `docs/vision`, `docs/engram`; `research`: `docs/research`. Use `GET /scopes` to list available scope names.

## scripts/ask

Thin bash wrapper for querying any Stargate model/pipeline.
Default model: `rag-answer` (query rewriting + RAG + grounded answer).

```bash
ask "What does research say about RRF?"          # default: rag-answer
ask -m phi-4-q4-k-m-16384 "Explain attention"    # specific model
ask -M arcee-ai/trinity:free "question"           # cloud model via rag-answer
ask -s research "question"                        # scope override
ask -o output.txt "question"                      # save to file
```

## scripts/validate-pipeline.py

Validate pipeline YAML, prompts.yaml, models.yaml before commit.

```bash
python scripts/validate-pipeline.py pipelines.local/
```

## scripts/model_manager/

TUI and CLI for catalog management, topology, services.

Subcommands: list, generate, measure, verify, download, lint, stats, update (llama-server/vLLM to latest release; remote targets from `scripts/model_manager/update_targets.py`).
TUI screens: Home, Catalog, Download, Measure, Remotes, Settings, Footprint.

## ./manage

Primary entry point for deployment and operations. Launches Textual TUI.

Handles:
- Federation env vars setup
- Config resolution
- Container lifecycle (build, start, health checks)
- `.env.local` loading

```bash
./manage              # Launch TUI
./manage relay        # Relay operations (headless)
./manage topology     # Print topology YAML
```

## Deployment Scripts

| Script | Purpose |
|---|---|
| `scripts/deploy-gpu-relay.sh` | Deploy relay topology (updates code in containers) |
| `scripts/build_golem_tarball.sh` | Build Golem deployment tarball |
| `scripts/deploy-to-registry.sh` | Deploy to registry |

## Validation Scripts

| Script | Purpose |
|---|---|
| `scripts/validate-event-factories.py` | Validate event factory definitions |
| `scripts/validate-telemetry-factories.py` | Validate telemetry factory definitions |
| `scripts/detect-event-violations.py` | Detect event contract violations |
| `scripts/validate-pipeline.py` | Validate pipeline YAML |

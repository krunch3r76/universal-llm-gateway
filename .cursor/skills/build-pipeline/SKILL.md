---
name: build-pipeline
description: "Build new Stargate pipelines or add versions — golden-path first pipeline in 10 minutes, directory layout, step authoring, and version promotion."
---

# Build Pipeline

## 1. Golden Path: First Pipeline in 10 Minutes

Create a minimal 1-step pipeline that runs end-to-end. Only add complexity after this works.

### Directory structure

```
pipelines/{domain}/
  models.yaml                    # Model aliases (shared across versions)
  v1/
    {domain}-v1.yaml             # Chain definition
    prompts.yaml                 # Prompt templates
```

### models.yaml

```yaml
models:
  default:
    model: phi-4-q4-k-m-16384   # Full model ID from /v1/models
```

### prompts.yaml

```yaml
prompts:
  greeting:
    description: "Generate a greeting"
    template: |
      The user said: {text}

      Respond with a friendly greeting.
```

### Chain YAML ({domain}-v1.yaml)

```yaml
schema_version: 6
id: my-pipeline
version: "1.0"
type: {domain}
output: greet

options:
  timeout_seconds: 60

steps:
  - name: greet
    type: generate
    model_ref: default
    prompt_ref: {domain}.v1.greeting
    handler_inputs:
      text: sourceNs.text
    generation_parameters:
      temperature: 0.7
```

### Validate and test

**MCP path (preferred — works for Cursor agents and MCP-connected cloud agents):**

```
pipeline(op="validate", pipeline_id="consult-planner")
→ {"valid": true, "pipeline": "consult-planner", "steps": 1, "models": [...], "errors": []}

pipeline(op="run", pipeline_id="consult-planner", messages=[{"role": "user", "content": "Hello!"}])
→ {"content": "...", "execution_id": "abc123", "duration_s": 2.1, "usage": {...}}

observability(operation="pipeline-trace", params={"execution_id": "abc123"})
→ step-by-step trace: model selection, timing, success/failure per step
```

**CLI path (fallback — for terminal sessions without MCP):**

```bash
python scripts/validate-pipeline.py pipelines/{domain}/

curl -X POST http://localhost:9999/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "my-pipeline", "messages": [{"role": "user", "content": "Hello!"}]}'
```

Local (bare) model one-shots — default `?pseudostream=true` (skill `local-chat-completions`). **Never** on pipeline model IDs.

If this works, proceed. If not, check the error triage in section 9.

---

## 2. Pre-flight for Real Pipelines

**Domain agnosticism (CRITICAL)**: Pipelines are generic infrastructure reused
across arbitrary inputs. ∀ design decision (prompt wording, handler logic,
config tunables, vocabulary): the solution must generalize beyond the test
queries used during development. Never hardcode topic-specific terms, add
logic that only helps one eval query, or shape prompts around known test
inputs. If an improvement only helps 3 known queries but wouldn't help a
novel query in the same domain, it's overfitting — find a structural fix.

Before building anything beyond the golden path:

1. **Read pipeline lessons**: `tasks/lessons/index.md` — filter for Domain == `pipeline`.
   Read full files for critical lessons (currently 2: shared imports, sub-pipeline fragments).

2. **Know the auto-loading rules**: when you edit files matching `pipelines/**/*.yaml` or
   `pipelines/**/*.py`, these cursor rules activate automatically:
   - `pipeline_ws.mdc` — directory conventions, namespace isolation, anti-patterns
   - `pipeline_patterns_ws.mdc` — verification/consensus patterns, exclude_self, chunked execution

3. **Decide scope**:
   - New pipeline domain → `pipelines/{domain}/v1/`
   - New version of existing domain → `pipelines/{domain}/v{X}/`
   - Named variant → `pipelines/{domain}/v{X}-{name}/`

---

## 3. Step-by-Step Workflow

### Step 1: Create directory

```bash
mkdir -p pipelines/{domain}/v1/
```

For pipelines that need custom handlers:

```bash
mkdir -p pipelines/{domain}/v1/handlers/
touch pipelines/{domain}/v1/handlers/__init__.py
```

### Step 2: Define model aliases

Create `pipelines/{domain}/models.yaml` (at domain level, shared across versions):

```yaml
models:
  phi4:
    model: phi-4-q4-k-m-16384
  qwen:
    model: qwen3-32b-awq-32768
```

Required fields: `models:` wrapper, `model:` per entry. Optional: `system_prompt`, `execution`.

### Step 3: Write prompts

Create `pipelines/{domain}/v1/prompts.yaml`:

```yaml
prompts:
  analyze:
    description: "Analyze the input"
    system_prompt: |
      You are an analytical assistant.
    template: |
      Analyze the following:

      {text}
```

Required: `prompts:` wrapper, `template:` per entry. Optional: `description`, `system_prompt`.

**FORBIDDEN in prompts.yaml**: `json_schema`, `generation_parameters` — these belong in chain YAML step config. Pipeline fails to start if present.

### Step 4: Write the chain YAML

See the golden path example in section 1. Required top-level fields:

| Field | Required | Purpose |
|---|---|---|
| `schema_version` | yes | Always `6` |
| `id` | yes | Pipeline ID (becomes virtual model ID) |
| `version` | yes | Version string |
| `type` | yes | Domain name (matches directory) |
| `steps` | yes | List of step definitions |
| `output` | yes | Name of the final output step |
| `options` | recommended | `timeout_seconds` and pipeline-wide config |

### Step 5: Custom handlers (if needed)

Most pipelines only need `type: generate` (built-in). Create custom handlers when you need
deterministic logic (parsing, filtering, aggregation, API calls).

**LESSON GATE**: Before writing handler imports, read
`tasks/lessons/pipeline-shared-relative-imports.md` — always `from .shared.X` (one dot),
never `..shared` or `...shared`.

Handler skeleton:

```python
from typing import override
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

class MyHandler(BaseHandler):
    step_type = "{domain}_mystep_v1"

    @override
    async def execute(self, step, context) -> StepOutput:
        text = self._resolve_input(
            context._registry.resolver, step, "text", step.handler_inputs
        )
        result = await self._call_model(
            self._resolve_model_alias(step.model_ref, context),
            prompt, step, context,
        )
        return StepOutput(raw=result.content)
```

Registration in `handlers/__init__.py` (loader injects `DomainRouter` — see exemplars
`pipelines/assertion_enrichment/v1/handlers/__init__.py`):

```python
from __future__ import annotations

from typing import TYPE_CHECKING

from .my_handler import MyHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    router.register_domain_handler_class(
        "{domain}", "{domain}_mystep_v1", MyHandler
    )
```

**Invariant**: `step_type` class attribute must exactly match the registration key.

### Step 6: Validate

**MCP**: `pipeline(op="validate", pipeline_id="{pipeline-id}")` — checks handler registration,
prompt refs, model refs, step dependencies, schema. No inference compute.

**CLI**: `python scripts/validate-pipeline.py pipelines/{domain}/`

### Step 7: Test

**MCP** (preferred):

```
pipeline(
    op="run",
    pipeline_id="{pipeline-id}",
    messages=[{"role": "user", "content": "test input"}],
    options={"max_length": 500}
)
```

Then inspect execution:

```
observability(operation="pipeline-trace", params={"execution_id": "<from pipeline>"})
```

**CLI** (fallback):

```bash
curl -X POST http://localhost:9999/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "{pipeline-id}", "messages": [{"role": "user", "content": "test input"}]}'
```

Pass pipeline options at top level — never use `extra_body` (that is an OpenAI Python SDK feature):

```bash
curl ... -d '{"model": "...", "pipeline_options": {"max_length": 500}, "messages": [...]}'
```

---

## 4. The v6 Golden Rule

**Every value is produced and consumed explicitly.**

- `handler_outputs` **produces** a named value: `result: step1.json.result`
- `handler_inputs` **consumes** that value: `input_data: step1.json.result`
- If not declared in `handler_outputs`, it does not exist for other steps
- Dependencies are computed automatically from `handler_inputs` — no manual `depends_on`

---

## 5. Decision Tree: Choosing the Right Primitive

| You need to... | Use | Handler code? |
|---|---|---|
| Make a single LLM call | `type: generate` | No |
| Parse, filter, aggregate, or call APIs | Custom handler extending `BaseHandler` | Yes |
| Idempotent service side-effect from pipeline options | Custom handler reading `context.options` (no `handler_inputs` chat binding) | Yes — see exemplars below |
| Break a large pipeline into modular fragments | `type: sub_pipeline` + `pipeline_ref: path/to/fragment.yaml` | No |
| Call an independent pipeline as a service | `type: pipeline_call_v1` with `pipeline_id` | No |
| Run a step for each item in a list | Add `map_over` + `map_inputs` to any step (map is NOT a type) | No |
| Iteratively refine until quality threshold | `type: assess_loop` | Depends |

**Options-driven service pipelines** (migration-class): when the caller passes structured
data via `options` (not chat `sourceNs.text`), handlers read `context.options` directly
and perform idempotent writeback. Exemplars:
- `pipelines/assertion_enrichment/v1/` — enrichment writeback via cortex `assertion_update`
- `pipelines/predicate_extract/v1/` — predicate extraction apply step

Invocation shape:

```
pipeline(op="run", pipeline_id="assertion-enrichment",
         messages=[{"role": "user", "content": "enrich"}],
         options={"assertion_id": 123, "claim": "...", "entity_id": "entity:foo"})
```

For fire-and-forget dispatch, use `pipeline(op="async", ...)` with optional
`result_delivery` — see `docs/architecture/async-pipeline-dispatch.md`.

**sub_pipeline vs pipeline_call_v1**:
- `sub_pipeline`: internal fragments that share the parent's `optionsNs` and receive `inputs` bindings. Co-located YAML. Used to decompose a single pipeline.
- `pipeline_call_v1`: calls a fully independent pipeline by its `id`. The callee has its own config, models, and options. Used for reusable services (e.g., `rag-context`).

**LESSON GATE**: When creating sub-pipeline fragments, read
`tasks/lessons/pipeline-subpipeline-version-field.md` — fragment YAML must NOT have
`version` or `schema_version`.

---

## 6. Quick Reference: Binding Syntax

Format: `{your_field_name}: {namespace}.{path}`

### Namespaces

| Namespace | Example | Value |
|---|---|---|
| `sourceNs` | `sourceNs.text` | User's message content |
| `optionsNs` | `optionsNs.threshold` | Pipeline options value |
| `{step_name}` | `analyze.json.result` | Previous step's JSON output field |
| `{step_name}` | `analyze.raw` | Previous step's raw text |
| `{step_name}` | `analyze.text` | Step's `.text` property (json.translation or json.text or raw) |
| `mapNs` | `mapNs.iteration.value` | Current map iteration item |

### Map iteration context

| Reference | Value |
|---|---|
| `mapNs.iteration.value` | Current item |
| `mapNs.iteration.index` | Index (0, 1, 2, ...) |
| `mapNs.iteration.key` | Key (dict iteration) |
| `mapNs.iteration.total` | Total count |

### Collecting map results

| Pattern | Meaning |
|---|---|
| `map_step.*.json.score` | All iterations |
| `map_step.0.json.score` | First iteration |
| `map_step.-1.json.score` | Last iteration |
| `map_step.qwen.raw` | By dict key |

### Sub-pipeline input bindings

Inside a sub-pipeline fragment, bind to parent-provided inputs:

```yaml
handler_inputs:
  answer: inputs.answer
  question: inputs.question
```

---

## 7. Iteration Workflow

For fire-and-forget runs: `pipeline(op="async", pipeline_id=…, messages=…, result_delivery={bus_thread, bus_from_agent, bus_to_agent, bus_subject})` then poll with `pipeline(op="result", execution_id=…)`.

### MCP-Powered Loop (preferred for agents)

Agents can run the full test-observe-modify-compare cycle through MCP tools:

```
# 1. Run pipeline (baseline)
result_a = pipeline(op="run", pipeline_id="{pipeline-id}", messages=[...])
→ {"content": "...", "execution_id": "aaa111", "duration_s": 3.2}

# 2. Inspect execution trace
observability(operation="pipeline-trace", params={"execution_id": "aaa111"})
→ step-by-step: model selection, timing, pass/fail per step

# 3. Diagnose issues from trace (failures, slow steps, wrong model)

# 4. Consult on the problem step — CLI preferred (pipeline_test consult);
#    MCP step-specific consult is not on the unified pipeline tool wire.

# 5. Edit prompt/config via filesystem tools

# 6. quality_gate(files=[...]) — lint + compile check
# 7. If handler code changed: manage(action="rebuild", service="gateway") — rebuild container
# 8. manage(action="wait_healthy", service="gateway", timeout=120) — wait for service ready
# 9. Re-run pipeline (candidate)
result_b = pipeline(op="run", pipeline_id="{pipeline-id}", messages=[...])
→ {"content": "...", "execution_id": "bbb222", "duration_s": 2.8}

# 10. Compare runs side-by-side
observability(operation="compare-runs", params={"run_a": "aaa111", "run_b": "bbb222"})
→ latency delta, step-level diffs, model changes

# 11. If failures, check what went wrong
observability(operation="recent-failures")

# 12. Verify code quality after handler changes (run before rebuild when handlers were edited)
quality_gate(files=["pipelines/{domain}/v1/handlers/my_handler.py"])
```

Repeat steps 4–9 until the pipeline meets quality criteria. The agent never
needs to ask the user to run things — it drives the full loop.

### CLI Loop (snapshot / replay / compare)

For terminal sessions or single-step prompt refinement without re-running the
full pipeline:

```bash
# 1. Run pipeline once to get a baseline
curl -X POST http://localhost:9999/v1/chat/completions \
  -d '{"model": "{pipeline-id}", "messages": [{"role": "user", "content": "test"}]}'

# 2. Snapshot the execution
python -m tools.pipeline_test snapshot {pipeline-id}

# 3. Inspect a step's inputs/outputs
python -m tools.pipeline_test inspect latest -s {step_name}

# 4. Edit your prompt in prompts.yaml, then replay just that step
python -m tools.pipeline_test replay latest -s {step_name}

# 5. Compare original vs replayed output
python -m tools.pipeline_test compare latest
```

For prompt engineering questions, use `pipeline_test consult` (RAG-augmented, scope auto-detected from model tier). Use `--cloud-only` for best results:

```bash
python -m tools.pipeline_test consult --latest {pipeline-id} \
  -s {step_name} -p "the output is too verbose" --cloud-only
```

Once the pipeline runs end-to-end, use the **refine-pipeline** skill
(`.cursor/skills/refine-pipeline/SKILL.md`) for structured iteration:
orient → diagnose → consult → fix → verify → apply.

---

## 8. Consult-Driven Development

Three paths for getting expert advice, depending on context.

**Model preference**: Prefer cloud models for consultation when the cloud proxy
is available. Cloud models produce substantially better prompt-engineering and
architecture advice than local models. Until a curated list of consultation-capable
local models exists, treat cloud as the default for all consultation paths.

### Tool selection

| Scenario | MCP (agents) | CLI |
|---|---|---|
| Step-specific prompt refinement | `pipeline_test consult` (see below) | `pipeline_test consult --latest {id} -s {step} -p "..." --cloud-only` |
| Broad prompting research | `rag(op="search", arguments='{"scope": "research_small_llm", "query": "..."}')` or `rag(op="answer", arguments='{"scope": "research", "question": "..."}')` | `scripts/consult -r researcher --scope research --cloud-only "..."` |
| Design (no pipeline yet) | `rag(op="answer", arguments='{"scope": "workflows", "question": "Design a pipeline that..."}')` | `scripts/consult -r architect --scope workflows --cloud-only "..."` |
| Design (reviewing files) | `rag(op="search", ...)` + `fs(sandbox="project", op="read", path=...)` on pipeline dir | `scripts/consult -r reviewer --cloud-only -f pipelines/{domain}/` |

Step-specific consultation uses **`pipeline_test consult`** (CLI) or `scripts/consult` —
not a separate MCP `pipeline_consult` tool on the unified `pipeline(op=…)` wire.

**Scope auto-detection** (applies to `pipeline_test consult`):
- Cloud model (`/` in ID) → `research`
- Local model → `research_small_llm`
- Unknown → `research` (broader coverage)
- Pipeline topology → `workflows` (use `rag(op="answer", ...)`/`scripts/consult` for this)

**CLI tools** (`pipeline_test consult` and `scripts/consult`) both call
`consult_lib.execute_consult()` — same RAG, same model selection, same
execution path.

### Design phase (before writing YAML)

```bash
scripts/consult -r architect --chain \
  --models openai/gpt-5.2 google/gemini-2.5-pro-preview \
  -f pipelines/{domain}/ \
  "Design a pipeline that {goal}. Use schema_version 6. \
   What steps are needed? What handler types? What data flows between steps?"
```

If the domain directory doesn't exist yet, provide reference pipelines:

```bash
scripts/consult -r architect --chain \
  --models openai/gpt-5.2 google/gemini-2.5-pro-preview \
  -f pipelines/answer_v1/ -f pipelines/consensus/v8.0/ \
  "Design a new pipeline domain '{domain}' that {goal}."
```

### Prompt refinement (after first run)

`pipeline_test consult` auto-detects the RAG scope from the step's model tier
(cloud model → `research`, local model → `research_small_llm`).
Use `--cloud-only` for best consultation quality:

```bash
python -m tools.pipeline_test consult --latest {pipeline-id} \
  -s {step_name} -p "problem description: the output misses edge cases" --cloud-only
```

Override scope manually when needed: `--scope research` (broader coverage).

### Review phase (before finalizing)

```bash
scripts/consult -r reviewer --chain \
  --models openai/gpt-5.2 google/gemini-2.5-pro-preview \
  -f pipelines/{domain}/ \
  "Review this pipeline for: binding correctness, timeout math, \
   handler_outputs coverage, prompt quality, and missing error handling."
```

### Available scopes

To discover all scopes at runtime (authoritative — reflects actual RAG corpus):

```
rag(op="list_scopes")
→ {"scopes": ["research", "research_small_llm", "workflows", ...], "details": {...}}
```

Common scopes for pipeline work:

| Scope | Content |
|---|---|
| `research_small_llm` | Research for small/local models |
| `research` | Research for large/cloud models (and broader coverage) |
| `workflows` | Pipeline architecture and orchestration patterns |
| `project` | Architecture docs, vision, engram (default for `scripts/consult`) |

### Key flags

| Flag | Tool | When |
|---|---|---|
| `--chain` | `scripts/consult` | Sequential: first model analyzes, second reviews |
| `--models M1 M2` | both | Specify exact models (space-separated) |
| `--cloud-only` | `scripts/consult` | Auto-select cloud models only |
| `--scope NAME` | both | Override auto-detected RAG scope |
| `--no-rag` | both | Disable RAG context entirely |
| `-f path` | `scripts/consult` | Inject file/directory as context (repeatable) |
| `--parallel` | `pipeline_test consult` | Independent perspectives instead of chained |

---

## 9. Error Triage

| Error message | Likely cause | Fix |
|---|---|---|
| `No handler for type '{X}'` | Handler `__init__.py` crashed during import (often `..shared` import) | Use `.shared.*` (one dot). Simulate loader — see lesson. |
| `Unknown model_ref 'optionsNs.X'` | Sub-pipeline fragment has `version`/`schema_version` | Remove both fields from fragment YAML. |
| `DAG error: depends on unknown step 'inputs'` | Same as above | Remove `version`/`schema_version` from fragment. |
| `Pipeline timeout after 180s` | Default `options.timeout_seconds` is 180s | Set it >= sum of step timeouts. |
| `KeyError` / `AttributeError` in handler | Binding path wrong or previous step didn't produce expected output | Use `pipeline_test inspect` on the source step. |
| `Found 'system' field in prompts.yaml` | Used `system:` instead of `system_prompt:` | Rename to `system_prompt:`. |
| `Missing required 'template' field` | Prompt entry missing `template:` | Add it. |
| `Invalid binding` | Wrong namespace syntax | Check: `sourceNs.`, `optionsNs.`, or step name prefix. |

---

## 10. Critical Invariants

These are enforced at load time or cause silent failures:

- **Prompt namespace**: `{domain}.{version}.{name}` — derived from directory path. `prompt_ref` must use the owning version's namespace.
- **Step type suffix**: `{domain}_{name}_v{X}` — prevents cross-version collision when multiple versions are loaded.
- **Timeout math**: `options.timeout_seconds` >= critical path sum of step timeouts.
- **Schema in chain YAML only**: `generation_parameters.response_format.schema` goes in the step config, never in `prompts.yaml`.
- **Sub-pipeline fragments**: no `version` or `schema_version` fields.
- **Handler imports**: `.shared.*` (one dot) from any nesting depth.

---

## 11. Deeper Reference

### Consumption policy (docstring-first)

| Surface | Role |
|---|---|
| Live module docstrings (`user_handlers`, `DomainRouter`, `HandlerRegistry`) | **Registration/load SOT** — prefer over skill when they disagree |
| `services/universal-stargate/systems/pipeline/QUICKSTART.md` | Bindings, handler sketch, options-driven branch |
| `docs/architecture/async-pipeline-dispatch.md` | Fire-and-forget / async dispatch topology |
| `docs/architecture/pipeline.md` (generated) | Internals inventory only — **not** authoring path |
| Exemplars under `pipelines/{domain}/v1/` | Migration-class templates (options-driven writeback) |

### Local stubs (skill-owned)

- **YAML schema digest**: `.cursor/skills/build-pipeline/yaml-reference.md` — `PipelineSpec` / options / step fields + binding namespaces
- **Handler API digest**: `.cursor/skills/build-pipeline/handler-reference.md` — `register_handlers(router)` + `BaseHandler` contract

### Pointers

- **YAML schema + bindings (deep)**: `services/universal-stargate/systems/pipeline/QUICKSTART.md` §§1–7 and `README.md` v6 spec — not generated `pipeline.md`
- **Handler registration + load contract (SOT)**: `user_handlers.py` module docstring + exemplar `handlers/__init__.py` files
- **Migration-class exemplars** (options-driven, no chat-centrism):
  - `pipelines/assertion_enrichment/v1/` — idempotent cortex writeback
  - `pipelines/predicate_extract/v1/` — single-step apply handler
- **Chat/generate golden path**:
  - Minimal: `pipelines/answer_v1/` (2 steps, pipeline-as-service call + generate)
  - Complex: `pipelines/consensus/v8.0/` (map fan-out, sub-pipelines, verification chains)
- **Refine-pipeline skill**: `.cursor/skills/refine-pipeline/SKILL.md` — structured iteration on existing pipelines (sandbox, replay, consult, compare)
- **Implementation-plan workflow** (when this pipeline is built as one phase of a multi-phase plan): skill `implementation-plan-workflow` — phase-doc structure, parallel-group/depends-on annotations, coordinator-mode dispatch, BEFORE/AFTER completeness rule. Read whenever the work building this pipeline lives at a path like `tmp/prompts/{name}/phase-N-{slug}.md`.
- **Cursor rules** (auto-load on pipeline files):
  - `.cursor/rules/pipeline_ws.mdc` — directory conventions, namespace isolation, anti-patterns
  - `.cursor/rules/pipeline_patterns_ws.mdc` — verification patterns, exclude_self, chunked execution

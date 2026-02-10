# Pipeline Quickstart Guide

Create your first pipeline in 10 minutes.

## Prerequisites

- Python 3.12+
- Stargate running (port 9999)
- At least one model loaded in Gateway

## Understanding v6 Schema

All pipelines in this guide use **schema version 6** (v6), which provides explicit, type-safe data flow.

### What is v6?

v6 uses `handler_inputs` and `handler_outputs` to explicitly declare how data flows between steps:

```yaml
steps:
  - name: step1
    handler_outputs:
      result: step1.json.result    # Declares output
      
  - name: step2
    handler_inputs:
      input_data: step1.json.result  # Consumes step1's output
```

**Key Benefits**:
- **Explicit data flow**: See exactly where values come from
- **Type safety**: Binding errors detected at parse time
- **Self-documenting**: Pipeline structure clear from YAML alone
- **Auto-dependencies**: No manual `depends_on` needed

### Binding Syntax

**Format**: `{field_name}: {namespace}.{field_path}`

**Common namespaces**:
- `sourceNs.text` - User input from request
- `optionsNs.{option}` - Pipeline configuration
- `{step_name}.json.{field}` - Previous step's JSON output
- `{step_name}.text` - Previous step's text output
- `loopNs.iteration` - Loop iteration context (inside loop body only)
- `mapNs.iteration.value` - Map iteration context (inside map step only)

**For complete v6 specification**, see [README.md v6 Schema Specification](README.md#v6-schema-specification).

### Migrating from v4

If you have existing v4 pipelines:

1. Replace `inputs: [step1, step2]` with:
   ```yaml
   handler_inputs:
     field1: step1.json.field
     field2: step2.json.field
   ```

2. Remove `depends_on:` (now computed from `handler_inputs`)

3. Add `handler_outputs:` to declare what each step produces

**See**: [v4/v5 to v6 Migration](README.md#v4v5-to-v6-migration) for complete migration steps.

---

## 1. Minimal Pipeline

Create a pipeline YAML file:

```yaml
# ~/.local/share/universal-stargate/pipelines/pipelines.d/tutorial/hello.yaml
schema_version: 6
id: hello-world
type: tutorial
version: 1

options:
  timeout_seconds: 30

steps:
  - name: greet
    type: generate
    model_ref: default_model
    prompt_ref: tutorial.greeting
    handler_inputs:
      text: sourceNs.text
    handler_outputs:
      text: greet.text

output: greet.text
```

Create the prompt:

```yaml
# ~/.local/share/universal-stargate/pipelines/pipelines.d/tutorial/prompts.yaml
prompts:
  greeting:
    description: "Generate a friendly greeting"
    template: |
      The user said: {text}
      
      Respond with a friendly greeting.
```

Create model reference:

```yaml
# ~/.local/share/universal-stargate/pipelines/pipeline_models.yaml
models:
  default_model:
    model: phi-3.5-mini-8192
```

### Pipeline-Scoped Prompts (Alternative Structure)

For better modularity, you can organize pipelines in subdirectories with co-located prompts:

```
tutorial/
├── models.yaml              # Shared model references
├── handlers/                # Shared handlers (optional)
└── hello-world/
    ├── hello-world.yaml     # Pipeline definition
    └── prompts.yaml         # Pipeline-specific prompts
```

**Benefits**:
- Self-contained (move directory = move everything)
- Clear ownership (prompts belong to one pipeline)
- No cross-pipeline dependencies

**Prompt namespace**: `tutorial.hello-world.greeting`

```yaml
# tutorial/hello-world/hello-world.yaml
steps:
  - name: greet
    prompt_ref: tutorial.hello-world.greeting  # Scoped to this pipeline
```

## 2. Test Your Pipeline

```bash
curl -X POST http://localhost:9999/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "hello-world",
    "messages": [{"role": "user", "content": "Hello there!"}]
  }'
```

### Passing Pipeline Options

If your pipeline uses `optionsNs` bindings, pass options via `pipeline_options`:

```bash
curl -X POST http://localhost:9999/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "hello-world",
    "messages": [{"role": "user", "content": "Generate content"}],
    "pipeline_options": {
      "max_length": 500,
      "style": "formal"
    }
  }'
```

**❌ Common Mistake**: Don't use `extra_body` in curl/HTTP requests:

```bash
# WRONG - extra_body is an OpenAI Python SDK feature, not an HTTP API field
curl ... -d '{"model": "...", "extra_body": {"pipeline_options": {...}}}'

# CORRECT - pipeline_options at top level
curl ... -d '{"model": "...", "pipeline_options": {...}}'
```

**Note**: `extra_body` is a feature of the OpenAI Python SDK that flattens nested fields. In direct HTTP requests (curl, httpx, etc.), use `pipeline_options` directly at the top level.

---

## 3. Creating a Handler

When built-in handlers aren't enough, create a custom handler.

### Define Input Type

```python
# ~/.local/share/universal-stargate/handlers/tutorial_handlers.py
from dataclasses import dataclass
from typing import override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

@dataclass
class WordCountInputs:
    """Input schema - validated at parse time."""
    text: str
    
class WordCountHandler(BaseHandler):
    """Count words in input text."""
    
    step_type = "word_count"
    input_type = WordCountInputs
    
    @override
    async def execute(self, step, inputs: WordCountInputs, runtime):
        words = inputs.text.split()
        count = len(words)
        
        return StepOutput(
            data={"count": count, "words": words},
            metadata={"handler": "WordCountHandler"}
        )

def register_handlers(router):
    """Called by Stargate on startup."""
    router.register_domain_handler_class("tutorial", "word_count", WordCountHandler)
```

### Use in Pipeline

```yaml
steps:
  - name: count
    type: word_count
    handler: tutorial_handlers:WordCountHandler
    handler_inputs:
      text: sourceNs.text
    handler_outputs:
      count: count.json.count
      words: count.json.words
```

### Restart Stargate

```bash
# Handlers are loaded at startup - stop and restart
pkill -f "universal-stargate"
./services/universal-stargate/scripts/start-stargate.sh debug &
```

---

## 4. Binding Syntax

Bindings connect data sources to handler inputs using dot notation. The left side is your arbitrary field name, the right side is the binding path.

**Binding Path Format**: `{namespace}.{field}[.nested_field]`

**Examples**:
```yaml
handler_inputs:
  my_text: sourceNs.text              # Namespace: sourceNs, Field: text
  threshold: optionsNs.max_value      # Namespace: optionsNs, Field: max_value
  data: step1.json.results.items      # Namespace: step (implicit), Step: step1, Path: json.results.items
```

**For detailed syntax rules**, see [README.md handler_inputs Format](README.md#handler_inputs-format).

---

### Source Namespace (sourceNs)

Pipeline input from the request:

```yaml
handler_inputs:
  text: sourceNs.text           # User's message content
  metadata: sourceNs.metadata   # Request metadata dict
```

### Options Namespace (optionsNs)

Pipeline configuration:

```yaml
options:
  threshold: 0.8
  models: ["phi", "qwen"]

steps:
  - name: process
    handler_inputs:
      threshold: optionsNs.threshold
      model_list: optionsNs.models
```

### Step References

Previous step outputs:

```yaml
steps:
  - name: generate
    handler_outputs:
      statements: generate.json.statements
      
  - name: verify
    handler_inputs:
      statements: generate.json.statements  # Reference previous step
      raw_output: generate.raw              # Raw handler output
```

### Field Paths

Navigate nested structures with dot notation:

```yaml
handler_inputs:
  # Step output JSON fields
  items: step1.json.data.items
  first_item: step1.json.data.items.0   # Array index
  
  # Wildcard collection (from map steps)
  all_results: map_step.*.json.score    # Collect from all iterations
```

---

## 5. Adding Retry & Timeout

### Pipeline-Level Timeout (All Steps)

**Required for long-running pipelines**:

```yaml
schema_version: 6
id: my-pipeline

options:
  timeout_seconds: 600  # Total time for entire pipeline (all steps)
```

**Default**: 180s (3 minutes) - **increase for long pipelines!**

### Step-Level Timeout (Individual Steps)

```yaml
steps:
  - name: slow_step
    timeout_seconds: 120        # Total time for this step (all retries)
    handler_timeout_seconds: 90 # Per-attempt limit (optional)
```

### Retry Policy

```yaml
steps:
  - name: flaky_step
    retry_policy:
      max_attempts: 3
      backoff_strategy: exponential  # fixed, linear, exponential
      initial_interval_seconds: 2.0
      jitter: true                   # ±25% randomization
```

### Complete Timeout Hierarchy

```
Pipeline Timeout (options.timeout_seconds: 600)
  ↓ wraps all steps
  Step 1 Timeout (step.timeout_seconds: 120)
    ↓ wraps this step only
    Retry Loop (retry_policy.max_attempts: 3)
      ↓ multiple attempts with backoff
      Handler Timeout (handler_timeout_seconds: 90)
        ↓ per-attempt limit
        handler.execute()
  
  Step 2 Timeout (step.timeout_seconds: 200)
    ↓ independent timeout for next step
    ...
```

**Critical**: `options.timeout_seconds` must be ≥ sum of all `step.timeout_seconds`

### Common Mistakes

**❌ Forgetting pipeline timeout**:
```yaml
# No options.timeout_seconds defined
# Falls back to 180s default
steps:
  - name: slow_step
    timeout_seconds: 300  # ← Can't work! Pipeline times out first
```

**✅ Set both correctly**:
```yaml
options:
  timeout_seconds: 400  # Pipeline: 400s

steps:
  - name: step1
    timeout_seconds: 150  # Step 1: 150s
  - name: step2
    timeout_seconds: 200  # Step 2: 200s
  # Total: 350s (within 400s limit)
```

---

## 6. Checkpointing

Enable checkpointing for long-running pipelines:

### Pipeline-Level Configuration

```yaml
schema_version: 6
id: long-pipeline

checkpoint:
  enabled: true
  strategy: per_step           # per_step, milestone, none
  storage_path: /tmp/checkpoints
  ttl_seconds: 86400           # 24 hours
```

### Per-Step Override

```yaml
steps:
  - name: expensive_llm_call
    checkpoint: milestone      # Always checkpoint this step
    
  - name: quick_transform
    checkpoint: false          # Skip checkpoint for fast steps
```

### Resume on Failure

If a pipeline fails mid-execution:

1. Fix the issue (model availability, handler bug, etc.)
2. Re-run with same input
3. Pipeline resumes from last checkpoint

---

## 7. Map/Reduce for Parallelism

### Fan-Out with Map

Execute a handler for each item in a list:

```yaml
options:
  verification_models:
    - phi-3.5-mini
    - qwen-2.5-7b
    - llama-3.2-3b

steps:
  - name: verify_all
    type: verification              # ← EXPLICIT handler type (not "map")
    handler: VerificationHandler
    map_over:
      model: optionsNs.verification_models
    map_inputs:
      model_ref: mapNs.iteration.value   # Current item
    handler_inputs:
      statements: generate.json.statements
    timeout_seconds: 60
    min_success_threshold: 0.6           # 60% must succeed
```

**Important**: `type: map` is **not allowed**. MAP is an execution mode triggered by presence of `map_over`/`map_inputs` fields, not a handler type.

### mapNs Reference

Inside a map step, access iteration context:

| Reference | Value |
|-----------|-------|
| `mapNs.iteration.value` | Current item (e.g., "phi-3.5-mini") |
| `mapNs.iteration.index` | Current index (0, 1, 2, ...) |
| `mapNs.iteration.key` | Key (for dict iteration) |
| `mapNs.iteration.total` | Total count |

### Collect Results with Wildcards

```yaml
- name: aggregate
  handler_inputs:
    all_results: verify_all.*.json.evaluations   # All iterations
    first_result: verify_all.0.json.evaluations  # Index 0
    last_result: verify_all.-1.json.evaluations  # Last
```

### Iterating Over Map Step Outputs

Iterate over outputs from a previous map step (useful when each model processes its own output):

```yaml
options:
  answer_models:
    qwen: qwen
    phi: phi
    llama: llama

steps:
  # Step 1: Generate answers in parallel
  - name: answer_all
    type: generate
    map_over:
      model: optionsNs.answer_models  # Dict-based for key access
    handler_outputs:
      text: answer_all.*.raw

  # Step 2: Each model decomposes its own answer (parallel)
  - name: decompose_all
    type: generate
    map_over:
      answer: answer_all.*  # ← Iterate over MapOutputCollection
    map_inputs:
      model_ref: mapNs.iteration.key      # "qwen", "phi", "llama"
      answer_text: mapNs.iteration.value.raw  # That model's answer
    handler_outputs:
      statements: decompose_all.*.json.statements
```

**Requirements:**
- Previous step must use **dict-based** `map_over` (not list)
- Use `step_name.*` syntax in `map_over`
- Access `mapNs.iteration.key` for the key, `mapNs.iteration.value` for the StepOutput

### Preventing Self-Verification with `exclude_self`

When a model verifies outputs from multiple producers, use `exclude_self` to automatically exclude the originator from the verifier pool:

```yaml
options:
  answer_models:
    qwen: qwen
    phi: phi
    gemma: gemma
  verifier_pool:
    - qwen
    - phi
    - gemma

steps:
  # Step 1: Multiple models generate answers
  - name: answer_all
    type: generate
    map_over:
      model: optionsNs.answer_models
    map_inputs:
      model_ref: mapNs.iteration.value
    handler_inputs:
      question: sourceNs.text
    handler_outputs:
      answer: answer_all.*.json.answer

  # Step 2: Verify each answer with OTHER models (not self)
  - name: verify_all
    type: verification
    map_over:
      answer: answer_all.*  # Iterate over all answers
    model_pool: optionsNs.verifier_pool
    exclude_self: true      # ← Exclude originator from pool
    selection: rotate       # How to select from remaining pool
    map_inputs:
      originator: mapNs.iteration.key        # "qwen", "phi", "gemma"
      answer_text: mapNs.iteration.value.json.answer
    handler_inputs:
      question: sourceNs.text
    handler_outputs:
      verdict: verify_all.*.json.verdict
```

**How it works**:
1. `model_pool` provides the full list of available verifiers
2. `exclude_self: true` removes `mapNs.iteration.key` (originator) from the pool
3. `selection` strategy picks from remaining models:
   - `rotate`: Round-robin (deterministic, balanced)
   - `random`: Random selection
   - `first`: Always use first available

**Invariant**: ∀ answer A from model M, verifiers ⊆ (verifier_pool ∖ {M})

**When to use**:
- ✅ Consensus/verification pipelines (prevent model verifying itself)
- ✅ Cross-validation patterns (each model validates others)
- ✅ Multi-model ensembles with independence requirements
- ❌ Single-model pipelines (no self-verification risk)
- ❌ When explicit verifier assignment needed

**Comparison with explicit lists**:

```yaml
# ❌ Manual (error-prone, must update for each producer)
- name: verify_qwen_answer
  verify_models: [phi, gemma]  # Must exclude qwen manually
  
- name: verify_phi_answer
  verify_models: [qwen, gemma]  # Must exclude phi manually

# ✅ Declarative (automatic, single source of truth)
- name: verify_all
  model_pool: optionsNs.verifier_pool
  exclude_self: true  # System handles exclusion
```

---

## 8. Debugging

### Execution Summaries

Enable detailed logging:

```yaml
options:
  save_execution_summary: true
  summary_format: markdown  # markdown, yaml, json, all
```

Summaries saved to: `~/.local/share/universal-stargate/execution_summaries/`

### Validation Errors

Run the validation script before committing:
```bash
python scripts/validate-pipeline.py pipelines.local/{domain}/
```

**Configuration file errors** (prompts.yaml, models.yaml):

| Error | Cause | Fix |
|-------|-------|-----|
| `Found 'system' field` | Used `system:` instead of `system_prompt:` | Rename to `system_prompt:` |
| `Found 'model_id' field` | Used `model_id:` instead of `model:` | Rename to `model:` |
| `Missing required 'template' field` | Prompt missing template | Add `template:` field |
| `Missing required 'model' field` | Model ref missing model | Add `model:` field |

**Pipeline structure errors:**

| Error | Cause | Fix |
|-------|-------|-----|
| "Invalid binding" | Bad namespace syntax | Check `sourceNs.`, `optionsNs.`, or step name prefix |
| "Step not found" | Reference to missing step | Verify step names match exactly |
| "Circular dependency" | A → B → A | Restructure step dependencies |
| "Handler not found" | Missing handler class | Check `register_handlers()` function |

### Logs

```bash
# Stargate logs
tail -f /tmp/logs/universal-stargate/*.log

# Pipeline execution
grep "pipeline" /tmp/logs/universal-stargate/*.log
```

---

## 9. Best Practices

### Handler Design

1. **Single responsibility**: One handler per step type
2. **Typed inputs**: Use `input_type` dataclass for validation
3. **Deterministic**: Same inputs → same outputs
4. **Stateless**: No instance state between executions

### Checkpoint Strategy

| Scenario | Strategy |
|----------|----------|
| Fast pipeline (<30s) | `none` or `milestone` |
| Long pipeline (>2min) | `per_step` |
| Expensive LLM calls | `milestone` on those steps |
| Development/testing | `per_step` for quick iteration |

### Retry Policies

| Scenario | Policy |
|----------|--------|
| Transient network issues | `max_attempts: 3, exponential` |
| Rate limiting | `max_attempts: 5, linear, initial: 5s` |
| Known flaky API | `max_attempts: 3, jitter: true` |
| Critical step | `max_attempts: 5, max_interval: 60s` |

### Map Step Thresholds

| Scenario | Configuration |
|----------|---------------|
| All must succeed | `min_success_threshold: null` (default) |
| Majority required | `min_success_threshold: 0.5` |
| At least 2 | `min_success_threshold: 2` |
| Fast fail | `fail_fast: true` |

---

## 10. Complete Example: Multi-Model Verification

```yaml
schema_version: 6
id: verified-answer-v1
type: verification
version: 1
description: "Generate answer with multi-model verification"

checkpoint:
  enabled: true
  strategy: milestone

options:
  verification_threshold: 0.8
  verifiers:
    - phi-3.5-mini
    - qwen-2.5-7b
    - llama-3.2-3b

steps:
  # Step 1: Generate initial answer
  - name: generate
    type: generate
    model_ref: main_model
    prompt_ref: verification.generate
    handler_inputs:
      question: sourceNs.text
    handler_outputs:
      answer: generate.json.answer
      confidence: generate.json.confidence
    timeout_seconds: 60
    checkpoint: milestone
    retry_policy:
      max_attempts: 2
      backoff_strategy: exponential

  # Step 2: Verify with multiple models (parallel)
  - name: verify_all
    type: verification              # ← EXPLICIT handler type (not "map")
    handler: verification_handlers:VerificationHandler
    map_over:
      model: optionsNs.verifiers
    map_inputs:
      model_ref: mapNs.iteration.value
    handler_inputs:
      answer: generate.json.answer
      question: sourceNs.text
    handler_outputs:
      verdict: verify_all.json.verdict
      reasoning: verify_all.json.reasoning
    timeout_seconds: 90
    min_success_threshold: 0.6
    fail_fast: true

  # Step 3: Aggregate verdicts
  - name: aggregate
    type: aggregate
    handler: verification_handlers:AggregateHandler
    handler_inputs:
      all_verdicts: verify_all.*.json.verdict
      threshold: optionsNs.verification_threshold
    handler_outputs:
      final_verdict: aggregate.json.verdict
      agreement_score: aggregate.json.agreement

  # Step 4: Conditional refinement
  - name: refine
    type: generate
    condition: "aggregate.json.agreement < 0.8"
    model_ref: main_model
    prompt_ref: verification.refine
    handler_inputs:
      original_answer: generate.json.answer
      feedbacks: verify_all.*.json.reasoning
    handler_outputs:
      refined_answer: refine.json.answer

  # Step 5: Select final output
  - name: select_output
    type: select
    handler_inputs:
      refined: refine.json.answer
      original: generate.json.answer
      use_refined: aggregate.json.agreement < 0.8
    handler_outputs:
      final: select_output.text

output: select_output.text
```

---

## Next Steps

- **v6 Schema**: Read the [v6 Schema Specification](README.md#v6-schema-specification) for complete format rules
- Read the full [README.md](README.md) for architecture details
- See [README_AI.md](README_AI.md) for AI agent navigation
- **External workspace?** See [EXTERNAL_WORKSPACE.md](EXTERNAL_WORKSPACE.md) for development guide
- Explore `core/schemas.py` for all configuration options
- Check `core/handlers/builtin.py` for built-in handler examples

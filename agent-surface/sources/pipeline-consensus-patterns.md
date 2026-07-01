<!-- target:* -->
# Pipeline Consensus Patterns

**Pipeline design patterns for consensus and verification workflows.**

## Self-Verification Prevention

**MANDATORY**: ∀ verification steps: originator ∉ verifiers

### Pattern: `exclude_self` with `model_pool`

**Use when**: Multiple producers, each verified by other models

```yaml
options:
  verifier_pool: [model_a, model_b, model_c]

steps:
  - name: produce_all
    map_over:
      model: optionsNs.verifier_pool
    handler_outputs:
      result: produce_all.*.json.result

  - name: verify_all
    map_over:
      result: produce_all.*
    model_pool: optionsNs.verifier_pool
    exclude_self: true        # ← Automatic originator exclusion
    selection: rotate         # rotate | random | first
    map_inputs:
      originator: mapNs.iteration.key
```

### Anti-Patterns

❌ **Explicit verifier lists** (manual, error-prone):
```yaml
- name: verify_model_a
  verify_models: [model_b, model_c]  # Must maintain per producer
```

❌ **No independence check** (silent violation):
```yaml
- name: verify
  model_ref: some_model  # Could be same as producer
```

✅ **Declarative exclusion** (automatic, enforced):
```yaml
exclude_self: true
model_pool: optionsNs.verifier_pool
```

## Invariants

| Pattern | Invariant | Enforcement |
|---|---|---|
| `exclude_self: true` | ∀ iteration key K, verifier ≠ K | map-executor pool selection |
| Consensus verification | ∀ answer A from model M, verifiers ⊆ (pool ∖ {M}) | map-config exclude_self |
| Independence | verifier.model_id ≠ content.originator_model_id | Provenance system |

## Selection Strategies

| Strategy | Use Case | Behavior |
|---|---|---|
| `rotate` | Balanced load, deterministic | `candidates[index % len(candidates)]` |
| `random` | Unpredictable distribution | `random.choice(candidates)` |
| `first` | Consistent single fallback | `candidates[0]` |

**Default**: `rotate` (reproducible, balanced)

## Validation

```bash
# Check for explicit verify_models (should migrate to exclude_self)
rg "verify_models:" pipelines.local/ --type yaml

# Verify exclude_self usage
rg "exclude_self: true" pipelines.local/ --type yaml
```

## Migration Path

1. Add `verifier_pool` to pipeline options
2. Add `exclude_self: true` to verification steps
3. Add `originator` to handler input schema (optional for backward compat)
4. Remove explicit `verify_models` lists
5. Validate via the pipeline validation script

## Chunked Model Execution

**Use when**: Handler processes multiple items (claims, statements) per model, configurable batch size

### Pattern: `execution` config in `models.yaml` → chunked model executor

```yaml
# models.yaml
llama_3_1_8b:
  model: meta-llama-3-1-8b-instruct-q8-0-32768-cpu
  execution:
    chunk_size: 4        # Statements per LLM call (1 = isolation mode)
    max_concurrent: 4    # Max parallel chunks
    timeout_ms: 15000    # Per-chunk timeout
    sequential: false    # Force sequential execution
```

### Handler integration pattern

```python
# 1. Resolve ModelRef (not just model ID string)
registry = context._registry
model_config = registry.get_model_config(alias, domain=context.pipeline.domain)
model_id = model_config.model

# 2. Extract execution config
exec_config = get_execution_config(model_config)

# 3. Branch on chunk_size
if exec_config.chunk_size == 1:
    # Single-item mode (TaskGroup)
    ...
else:
    # Chunked mode (ChunkedModelExecutor)
    executor = ChunkedModelExecutor(
        model_selector=FirstAvailable([model_id]),
        chunk_strategy=create_chunk_strategy(exec_config),
        max_concurrent=exec_config.max_concurrent,
        timeout_per_chunk_ms=exec_config.timeout_ms,
    )
    result = await executor.execute(items, process_fn)
```

### Invariants

| Invariant | Enforcement |
|---|---|
| ∀ item: processed exactly once | Chunked executor merge |
| ∀ chunk_error: fallback ∨ propagated | Fallback-handler strategy |
| `chunk_size >= 1` | Model-execution-config post-init |
| `sequential ⟹ max_concurrent = 1` | Model-execution-config post-init |

## Pipeline-as-Service Pattern

**Use when**: A multi-step capability (RAG retrieval+rewrite, consensus, etc.) should be
callable by multiple pipelines without duplicating handler code.

A pipeline with a known `id` becomes a **virtual model ID** — any handler can invoke it
via the model-call helper. The gateway detects the pipeline ID at request time and
routes to the pipeline executor transparently.

### Current service pipelines

| Pipeline ID | Description | Best for |
|---|---|---|
| `rag-context` | Rewrites query → parallel RAG retrieval + RRF merge → returns context chunks | Any pipeline needing RAG injection |
| `rag-answer` | Calls `rag-context` (pipeline-as-service) → generates grounded answer | Single-call RAG Q&A endpoint |

### Calling a pipeline from a handler

```python
class MyHandler(BaseHandler):
    step_type = "my_step_v1"

    async def execute(self, step, context) -> StepOutput:
        # Call rag-context for retrieval — returns assembled context chunks
        # Use rag-answer instead to get a fully generated answer
        result = await self._call_model(
            "rag-context",                 # Virtual model ID = pipeline id
            context.source_text,           # Question / user input
            step,
            context,
            system_prompt=None,            # Pipeline's own system prompt applies
            temperature=0.3,
        )
        rag_answer = result.content

        # ... use rag_answer in further processing ...
        return StepOutput(raw=rag_answer)
```

### Calling with `pipeline_options`

Pass parameters into the target pipeline's `optionsNs` via the OpenAI-compatible
`pipeline_options` field. Use a direct HTTP call when `pipeline_options` must be
passed (the standard proxy client strips unknown fields):

```python
import httpx

async def _call_pipeline_with_options(
    pipeline_id: str,
    question: str,
    options: dict,
    stargate_url: str = "http://localhost:9999",
) -> str:
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{stargate_url}/v1/chat/completions",
            json={
                "model": pipeline_id,
                "messages": [{"role": "user", "content": question}],
                "stream": False,
                "pipeline_options": options,
            },
        )
        resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]
```

### From a pipeline test/consult CLI

Use a `--rag-pipeline` flag to have the `rag-context` pipeline supply assembled
context to normal ask/consult models (query rewriting + parallel retrieval +
RRF merge). Use a `--models rag-answer` variant (without `--rag-pipeline`) to
route the entire question through the full pipeline including generation — a
single-model answer rather than a two-step retrieval+consultation flow.

### Invariants

- ∀ pipeline-as-service call: goes through the gateway → full observability via events
- ∀ pipeline-as-service: timeout must account for the called pipeline's total runtime
- ∀ caller: `step.timeout_seconds` must be ≥ callee `options.timeout_seconds`
- Recursive pipeline calls are not detected at validation time — avoid them

### Anti-patterns

❌ Duplicating RAG retrieval logic in every handler instead of calling `rag-context`
❌ Direct HTTP client to the RAG service when intelligent query rewriting is needed
❌ Registering a handler in the callee's domain from the caller's `__init__.py`
<!-- /target:* -->

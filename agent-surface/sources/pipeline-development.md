<!-- target:* -->
# Pipeline Development

**Pipeline development patterns for variant pipelines in subdirectories** (gateway domain).

## Directory Structure
**Invariant**: ∀ variant: develop in subdirectory with versioned structure

```
pipelines.local/{pipeline_name}/
  v{X}/           # Versioned variants
  v{X}.{Y}/       # Minor iterations
  README_AI.md    # Root documentation
  CONTRACTS.md    # API contracts
  PROVENANCE.md   # Rationale & evolution
```

### Example: Consensus Pipeline
```
pipelines.local/consensus/
  v3/            # Major version
  v3.1/          # Minor iteration
  v3.2/          # Current variant
  v4-analytical/ # Named variant
  README_AI.md
  CONTRACTS.md
  PROVENANCE.md
```

## Variant Development
| Action | Pattern |
|--------|---------|
| New major version | `v{X}/` |
| Minor iteration | `v{X}.{Y}/` |
| Named variant | `v{X}-{name}/` |
| Experimental | `v{X}-{experiment}/` |

## Subdirectory Contents
Each variant MUST contain:
- `basic-v{X}.{Y}.yaml` or equivalent pipeline config
- `handlers/__init__.py` + handler modules
- `prompts.yaml` (if applicable)
- `README_AI.md` (variant-specific docs)

Optional:
- `FROZEN.md` (marks stable/archived version)
- `LESSONS_LEARNED.md` (retrospective)
- `test-v{X}.{Y}.sh` (test script)

## Rationale
- **Parallel development**: Multiple variants co-exist
- **Clean rollback**: Previous versions remain intact
- **Documentation**: Each variant documents its changes
- **Testing**: Isolated test scripts per variant

## Step Type Naming — Version Isolation

**Invariant**: ∀ version V: step types registered by V use `*_v{V}` suffix

All versions load into the same process. `register_domain_handler_class` has
last-write-wins semantics, so identically named step types from different
versions silently collide (alphabetical load order → highest version wins).

| Version | Step type pattern | Example |
|---|---|---|
| v4.0 | `consensus_{name}_v4` | `consensus_verify_chain_v4` |
| v5.0 | `consensus_{name}_v5_0` | `consensus_verify_chain_v5_0` |
| v5.1 | `consensus_{name}_v5_1` | `consensus_verify_chain_v5_1` |

**Exception**: Cross-version step type reuse (e.g., `consensus_answer_v3_3` used
by v5.0/v5.1) is permitted when the handler code is owned by the source version
and not overridden.

❌ `consensus_verify_chain_v4` registered by both v4.0 and v5.0 (collision)
✅ `consensus_verify_chain_v5_0` registered only by v5.0 (isolated)

## step_type Attribute — Must Match Registration Key

**Invariant**: ∀ handler H: H.step_type == key passed to register_domain_handler_class

The `step_type` class attribute is used for logging and introspection. The registration
key (in `handlers/__init__.py`) is used for dispatch. They refer to the same step — drift
between them corrupts log traces and makes debugging misleading without any runtime error.

❌ `step_type = "consensus_analyze_v4"` + `register_domain_handler_class(..., "consensus_analyze_v5_0", ...)`
✅ `step_type = "consensus_analyze_v5_0"` matches registration key exactly

**Verification after adding or modifying a handler**:
```bash
grep -n 'step_type' pipelines/consensus/v6.1/handlers/*.py
grep -n 'register_domain_handler_class' pipelines/consensus/v6.1/handlers/__init__.py
# Every step_type value must appear verbatim as a registration key
```

## Prompt Namespace Isolation

**Invariant**: ∀ version V: prompt_ref ∈ `{domain}.{V}.*`

Each version's `prompts.yaml` is loaded under namespace `{domain}.{version}`
(derived from directory path). All prompt_refs MUST reference the version's
own namespace — cross-version prompt references are forbidden.

| Version | Prompt namespace | Example ref |
|---|---|---|
| v6.0 | `consensus.v6.0` | `consensus.v6.0.analyze_question` |
| v6.1 | `consensus.v6.1` | `consensus.v6.1.verify_specific` |

**Enforced at load time**: pipeline registry validation rejects
prompt_refs that don't start with `{domain}.{source_variant}.`

Handler fallback refs (e.g. `or "consensus.v6.0.classify_atomicity"`) must
also use the owning version's namespace.

❌ `prompt_ref: consensus.v5.0.answer` in v6.1 YAML (cross-version)
✅ `prompt_ref: consensus.v6.1.answer` in v6.1 YAML (self-contained)

## Events & Observability

**Invariant**: Handlers MUST NOT emit pipeline events. All events are framework-level.

The DAG executor emits lifecycle events for every step automatically:
- `StepStarted` / `StepCompleted` / `StepFailed` — per step (recorder + bus)
- `StepInputsCaptured` / `StepOutputCaptured` — per step (recorder)
- `ModelInvocation` — per model-call (recorder, emitted by the base handler)
- `PipelineStarted` / `PipelineCompleted` / `PipelineFailed` — per execution

Custom handlers get full observability by inheriting the base handler — no
event code needed. A pipeline viewer consumes these events to display step
status, timing, and model calls.

### Querying Pipeline Events

All pipeline events flow to the Event Service. Query via the events CLI
(pipeline-trace, compare-runs ops) or the equivalent MCP observability
operation when available.

**Exception**: Handlers that manage opaque sub-step lifecycles (e.g., an
assess-loop's iteration cycles) MAY emit pipeline-event subclasses directly to
the per-execution JSONL recorder. These are observability events — not bus
coordination signals — and are justified when the framework cannot observe
intra-step loop semantics. ∀ such event: written to recorder only, ¬published
to the event bus.

## StepConfig Domain Fields

**Invariant**: ∀ pipeline-specific config: use domain fields (model_extra), ¬first-class StepConfig attrs

`StepConfig` uses `extra="allow"`. Unknown YAML keys become domain fields in `model_extra`,
accessed via `step.get_domain_field("key")`. Adding a new first-class attribute to `StepConfig`
**silently breaks** all handlers that read the same key via `get_domain_field()` — pydantic
consumes it as a known field instead of routing it to `model_extra`.

| ✅ Safe | ❌ Breaks existing handlers |
|---|---|
| `step.get_domain_field("model_pool")` | Adding `model_pool` to `StepConfig` class |
| Base-handler helper reads domain field | `step.model_pool` as first-class attr |

Shared model-resolution helpers live on the base handler class.

## Shared Module Import Depth (CRITICAL)

**Invariant**: ∀ import of `shared/` utilities: dot depth must match directory depth

Pipeline variants often place `shared/` as a sibling of `handlers/`, `verify/`, and `veto/`:

```
v{X}/
  handlers/       ← depth 1 from v{X}/
  verify/
    handlers/     ← depth 2 from v{X}/
  veto/
    handlers/     ← depth 2 from v{X}/
  shared/         ← sibling of all the above
```

The loader uses a flat synthetic package name with multiple submodule search
locations. All relative imports resolve against this flat name: `.` = the
package itself, `..` = above top-level → **crash**.

Required import prefix from ANY caller depth:

| Caller location | Import prefix | Mechanism |
|---|---|---|
| `v{X}/handlers/*.py` | `from .shared.X` | `.shared` found via parent search loc `v{X}/` ✅ |
| `v{X}/verify/handlers/*.py` | `from .shared.X` | same: one dot, found via parent ✅ |
| `v{X}/veto/handlers/*.py` | `from .shared.X` | same ✅ |
| `v{X}/handlers/__init__.py` → `verify/` | `from .verify.handlers` | `.verify` found via parent ✅ |

**Failure mode**: Using `..shared` or `...shared` causes `ImportError: attempted relative import
beyond top-level package`. This kills the entire `__init__.py` before any handler registers —
ALL step types go unregistered, pipeline YAML loads fine but validation immediately removes
it with "No handler for type" errors. No startup message names the broken file.

## Sub-pipeline Fragment Fields (CRITICAL)

**Invariant**: ∀ sub-pipeline fragment YAML: `version` ∉ YAML ∧ `schema_version` ∉ YAML

Pipeline loading uses the presence of `version` or `schema_version` to decide
whether to treat a YAML as a top-level pipeline. Sub-pipeline fragments
(`verify.yaml`, `veto.yaml`, `synthesize.yaml`) are loaded on-demand via
`pipeline_ref` resolution — they must NOT have these fields.

**Failure mode**: Fragment with `version` is validated as a standalone pipeline and fails with:
- `Unknown model_ref 'optionsNs.X'` — `optionsNs.*` only resolves inside a parent that defines `options:`
- `DAG error: Step 'X' depends on unknown step 'inputs'` — `inputs.*` bindings only resolve during sub-pipeline expansion

Both errors look like YAML schema bugs; the root cause is the spurious `version` field.

| Field | Top-level chain YAML | Sub-pipeline fragment |
|---|---|---|
| `schema_version` | ✅ required | ❌ omit |
| `version` | ✅ required | ❌ omit |
| `id` | ✅ required | ✅ required (for logging) |
| `inputs` | — | ✅ declares expected bindings |

## Pipeline-as-Service (Inter-Pipeline Calls)

Any pipeline can call another pipeline by its `id` as a **virtual model ID** via
the model-call helper in a handler. The gateway detects the ID at request time
and routes to the pipeline executor transparently.

**Current service pipelines**:
| `rag-context` | Query rewrite → parallel RAG retrieval + RRF merge → returns context chunks |
| `rag-answer` | Calls `rag-context` → generates grounded answer |

```python
# Call rag-context from a handler — injects retrieved chunks as context
result = await self._call_model(
    "rag-context",         # Virtual model ID = pipeline id
    question,              # User input becomes context.source_text in callee
    step,
    context,
)
rag_answer = result.content
```

**Invariant**: ∀ pipeline-as-service call: `step.timeout_seconds` ≥ callee's `options.timeout_seconds`

## Prompt Domain Neutrality

**Invariant**: ∀ pipeline prompt: examples and vocabulary are domain-neutral unless
the pipeline is explicitly scoped to a single domain.

Prompts are reused across arbitrary questions. Domain-specific examples (medical
terminology, named drugs, specific biochemical pathways, etc.) anchor the model's
reasoning to the test question used during development and degrade quality on
unrelated inputs.

| ❌ Domain-specific (from test question) | ✅ Domain-neutral |
|---|---|
| `"renal impairment" = "kidney dysfunction"` | `"increases X" = "elevates X"` |
| `"inhibits gluconeogenesis"` | `technical term = plain equivalent` |
| Split "Causes: Environmental / Causes: Genetic" | Split "Aspect A / Aspect B" |

**Detection**: before committing a prompt, scan for proper nouns, named conditions,
named compounds, or any vocabulary that belongs to one subject domain.

## Prompt Configuration (CRITICAL)

**Invariant**: ∀ `prompts.yaml`: allowed fields = `{description, system_prompt, template}` only

`json_schema` and `generation_parameters` are **forbidden in `prompts.yaml`** and enforced at load time
(pipeline fails to start with a clear error). Both belong exclusively in step config.

| Field | Where it belongs |
|---|---|
| `json_schema` | `generation_parameters.response_format.schema` in chain YAML step |
| `generation_parameters` | `generation_parameters:` in chain YAML step |
| `system_prompt` | `prompts.yaml` (prompt-level default) OR `model.system_prompt` (model-level fallback) |

```yaml
# ✅ Correct — schema in step config
steps:
  - name: analyze
    type: generate
    prompt_ref: myns.analyze
    generation_parameters:
      temperature: 0.3
      response_format:
        type: json_object
        schema:
          type: object
          properties:
            result: {type: string}
          required: [result]

# ❌ Forbidden — schema in prompts.yaml (pipeline will fail to start)
prompts:
  myns.analyze:
    template: "..."
    json_schema:           # ← REJECTED at load time
      type: object
      ...
```

## Anti-patterns
❌ Flat structure: `pipelines.local/consensus/handler_v2.py`
❌ Overwriting: Modifying active version without new directory
❌ Unnamed variants: `pipelines.local/consensus/new/`
❌ Shared step type names across versions (silent override)
❌ `step_type` attribute that doesn't match the registration key in `__init__.py`
❌ Cross-version prompt_refs (e.g. v6.1 referencing `consensus.v5.0.*`)
❌ Adding pipeline fields to `StepConfig` (breaks domain field access)
❌ Emitting events from handlers (framework handles this)
❌ `from ..shared.X` or `from ...shared.X` in any handler — `..` goes above the flat top-level package
❌ `from ..verify.handlers` in `v{X}/handlers/__init__.py` — same issue, use one dot
❌ `version: "X"` in sub-pipeline fragment YAML — triggers standalone validation, fails on `optionsNs.*` and `inputs.*`
❌ Duplicating RAG retrieval in handlers — call `rag-context` instead
❌ `json_schema` in `prompts.yaml` — pipeline fails to start; use `generation_parameters.response_format.schema` in chain YAML

✅ Versioned subdirs: `pipelines.local/consensus/v3.2/`
✅ Named variants: `pipelines.local/consensus/v4-analytical/`
✅ Documentation: Root-level contracts + variant-specific READMEs
✅ Version-namespaced step types: `consensus_{name}_v5_0`
✅ Version-namespaced prompt_refs: `consensus.v6.0.analyze_question`
✅ Domain fields for handler-specific config: `get_domain_field()`
✅ Base-handler helpers for model resolution
✅ `from .shared.X` in ALL handler files — one dot, found via the search locations
✅ Sub-pipeline fragment YAMLs have `id` + `type` + `inputs` + `steps` — no `version` or `schema_version`
✅ Shared capabilities (RAG, consensus) implemented as service pipelines, called via virtual model ID
<!-- /target:* -->

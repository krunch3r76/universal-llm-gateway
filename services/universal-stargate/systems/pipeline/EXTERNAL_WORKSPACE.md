# Pipeline Development Guide for External Workspaces

**For agents/humans working in separate workspaces with access to `./pipelines.local/`**

---

## Quick Start

### Reading Order (Required)

1. **[README_AI.md](README_AI.md)** - AI navigation, patterns, invariants
2. **[README.md#v6-schema-specification](README.md#v6-schema-specification)** - Binding format rules
3. **[QUICKSTART.md](QUICKSTART.md)** - Examples and tutorials

### File Location

```
./pipelines.local/
├── fragments.d/              # Reusable fragments (optional)
│   └── {fragment-id}.yaml
└── {domain}/
    ├── {pipeline-id}.yaml    # Pipeline file
    ├── models.yaml           # Model references
    ├── prompts.yaml          # Prompt templates
    └── handlers/             # Custom handlers (optional)
        └── __init__.py
```

### Pipeline-Scoped Prompts

Each pipeline lives in its own subdirectory with co-located prompts:

```
{domain}/
├── models.yaml           # Shared model references
├── handlers/             # Shared handlers
└── {pipeline-id}/
    ├── {pipeline-id}.yaml
    └── prompts.yaml      # Pipeline-specific prompts
```

**Prompt namespace**: `{domain}.{pipeline-id}.{prompt_name}`

**Example**:
```yaml
# erotica/episode-main/prompts.yaml
prompts:
  main_episode:
    template: |
      ...

# erotica/episode-main/episode-main.yaml
steps:
  - name: generate
    prompt_ref: erotica.episode-main.main_episode
```

---

## Validation (MANDATORY)

**Run before every commit:**

```bash
# Script should be in PATH
validate-pipeline.py pipelines.local/{domain}/

# Or with full path:
python /mnt/torus/projects/universal-llm-gateway/scripts/validate-pipeline.py pipelines.local/{domain}/
```

**Validates:**
- ✅ Pipeline structure (schema version, steps, dependencies)
- ✅ prompts.yaml structure (`system_prompt:` not `system:`)
- ✅ models.yaml structure (`model:` not `model_id:`)
- ✅ handler_inputs/outputs format

---

## Configuration Files (Critical Rules)

### prompts.yaml

```yaml
# ✅ REQUIRED: Top-level prompts: wrapper
prompts:
  my_prompt:
    system_prompt: |
      System prompt content.
    template: |
      User prompt with {{variables}}.
```

**Fields**: `template` (required), `system_prompt`, `description`, `json_schema`

**Note**: `generation_parameters` NO LONGER supported in prompts.yaml. Use step config only.

### models.yaml

```yaml
# ✅ REQUIRED: Top-level models: wrapper, field name is "model:" not "model_id:"
models:
  my_model_ref:
    model: model-id-with-context-16384-hybrid  # Exact ID from catalog
```

**Check available models:** `curl -s http://localhost:9999/v1/models | jq -r '.data[].id'`

### References in Pipelines

- **Prompts**: Use namespace prefix → `prompt_ref: "domain.prompt_name"`
- **Models**: No namespace → `model_ref: "my_model_ref"`

---

## Fragments (Optional)

**Purpose**: Reusable step sequences for DRY pipeline composition.

### File Location

```
./pipelines.local/
├── fragments.d/              # Shared fragments
│   └── {fragment-id}.yaml
└── {domain}/
    └── {pipeline-id}.yaml    # Uses fragments with "use:"
```

### Basic Usage

**Fragment definition** (`fragments.d/two-step.yaml`):
```yaml
fragment:
  id: two_step_process
  steps:
    - name: generate
      type: generate
      model_ref: "{model}"          # Variables with {brackets}
      handler_outputs:
        result: generate.json.text
    
    - name: validate
      type: transform
      handler_inputs:
        text: generate.json.text    # ← Internal reference
      handler_outputs:
        valid: validate.json.valid
```

**Usage in pipeline**:
```yaml
steps:
  - use: two_step_process
    with:
      model: phi-3.5-mini           # Substitutes {model}
    as_prefix: stage1               # Prefixes step names

  # Expands to:
  # - name: stage1_generate
  #   handler_outputs:
  #     result: stage1_generate.json.text  # ← Automatically prefixed!
  # - name: stage1_validate
  #   handler_inputs:
  #     text: stage1_generate.json.text    # ← Automatically prefixed!
```

### V6 Only (CRITICAL)

- ✅ Use `handler_inputs` and `handler_outputs` (v6 bindings)
- ❌ **DO NOT use** `depends_on`, `inputs`, `from` (legacy fields - not supported)

**Anti-pattern**: See [README_AI.md#ap-frag-01](README_AI.md#ap-frag-01) for legacy field errors.

### Key Features

- **Variable substitution**: `with:` injects values (type-preserving: floats stay floats)
- **Automatic prefixing**: `as_prefix` updates internal step references in bindings
- **External bindings unchanged**: `sourceNs`, `optionsNs`, `loopNs` not modified

**Full documentation**: [README_AI.md#fragment-composition-with-v6-bindings](README_AI.md#fragment-composition-with-v6-bindings)

---

## Workflow Checklist

- [ ] Check available models: `curl -s http://localhost:9999/v1/models | jq -r '.data[].id'`
- [ ] Create `{domain}/prompts.yaml` with `prompts:` wrapper
- [ ] Create `{domain}/models.yaml` with `models:` wrapper, use `model:` field
- [ ] Create pipeline with `schema_version: 6`
- [ ] Use namespaced `prompt_ref` and flat `model_ref`
- [ ] Validate: `validate-pipeline.py pipelines.local/{domain}/`
- [ ] Fix all validation errors before commit

---

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| `system:` in prompts.yaml | Use `system_prompt:` |
| `model_id:` in models.yaml | Use `model:` |
| `prompt_ref: "main"` | Use `prompt_ref: "domain.main"` |
| Missing model suffix | Check catalog for exact ID including `-cpu`/`-hybrid` |
| `depends_on:` in v6 | Remove - computed from `handler_inputs` |
| Flat `temperature: 0.5` | Use `generation_parameters: {temperature: 0.5}` |
| `generation_parameters` in prompts.yaml | Move to step config (validation error) |
| Legacy fields in fragments (`depends_on`, `inputs`, `from`) | Use v6 `handler_inputs`/`handler_outputs` only |

---

## See Also

- **Patterns & Anti-patterns**: [README_AI.md](README_AI.md)
- **Schema Details**: [README.md#v6-schema-specification](README.md#v6-schema-specification)
- **Handler Contract**: [README.md#handler-protocol](README.md#handler-protocol)
- **Examples**: [QUICKSTART.md](QUICKSTART.md)

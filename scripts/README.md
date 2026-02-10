# Scripts

## validate-pipeline.py

Validates v6 pipeline YAML files **and configuration files** (`prompts.yaml`, `models.yaml`) against schema rules.

### Usage

```bash
# Validate single file (pipeline or config)
./scripts/validate-pipeline.py pipelines.local/consensus/basic.yaml
./scripts/validate-pipeline.py pipelines.local/erotica/prompts.yaml

# Validate directory (recursive) - validates ALL YAML files
./scripts/validate-pipeline.py pipelines.local/consensus/

# Validate all local pipelines and configs
./scripts/validate-pipeline.py pipelines.local/
```

### What It Checks

**Pipeline files** (via `PipelineValidator`):
- Step references validity
- Binding structure and field paths
- Circular dependencies

**Configuration files** (NEW):

**prompts.yaml:**
- ✅ `prompts:` top-level wrapper exists
- ✅ Required `template` field present
- ✅ **Catches `system:` instead of `system_prompt:`** (common mistake!)
- ✅ Validates allowed fields: `template`, `system_prompt`, `description`, `json_schema`, `generation_parameters`
- ✅ Type checks (json_schema must be dict, etc.)

**models.yaml:**
- ✅ `models:` top-level wrapper exists
- ✅ Required `model` field present
- ✅ **Catches `model_id:` instead of `model:`** (common mistake!)
- ✅ Validates allowed fields: `model`, `system_prompt`, `description`

**v6-specific checks**:
- Schema version is 6
- No `depends_on` usage (v6 computes from `handler_inputs`)
- Namespace prefixes valid (sourceNs, optionsNs, loopNs, mapNs, step names)
- handler_outputs bindings have field paths
- Flat generation parameters not allowed (must use `generation_parameters` dict)

### Exit Codes

- `0` - All pipelines valid
- `1` - Validation errors found
- `2` - Script error (file not found, import error, etc.)

### Requirements

- Run from project root
- Virtual environment active (`~/.venvs/universal`)
- Python 3.12+

### Examples

**Valid directory (all files pass):**
```
Validating 5 file(s)...

✓ pipelines.local/erotica/episode-main.yaml [pipeline]
✓ pipelines.local/erotica/micro-scene-pov.yaml [pipeline]
✓ pipelines.local/erotica/prompts.yaml [prompts config]
✓ pipelines.local/erotica/models.yaml [models config]

──────────────────────────────────────────────────
Results: 4/4 passed
  - 2 pipeline(s)
  - 1 prompts config(s)
  - 1 models config(s)

✓ All files valid
```

**Invalid configuration file:**
```
Validating 3 file(s)...

✓ pipelines.local/test/basic.yaml [pipeline]
✗ pipelines.local/test/prompts.yaml [prompts config]
  │ Prompt 'main_episode': Found 'system' field. Did you mean 'system_prompt'? Valid fields: template, system_prompt, description, json_schema, generation_parameters
  │ Prompt 'main_episode': Unknown fields: {'system'}. Allowed: {'template', 'json_schema', 'generation_parameters', 'description', 'system_prompt'}
✗ pipelines.local/test/models.yaml [models config]
  │ Model 'my_model': Found 'model_id' field. Should be 'model' (not 'model_id')
  │ Model 'my_model': Missing required 'model' field

──────────────────────────────────────────────────
Results: 1/3 passed
  - 1 pipeline(s)
  - 1 prompts config(s)
  - 1 models config(s)

✗ 2 file(s) have errors

See: services/universal-stargate/systems/pipeline/README.md#v6-schema-specification
```

**Invalid pipeline:**
```
✗ pipelines.local/test/invalid.yaml [pipeline]
  │ Expected schema_version: 6, got 5. See: README.md#v6-schema-specification
  │ Step 'merge': v6 doesn't use 'depends_on' (computed automatically from handler_inputs)

──────────────────────────────────────────────────
Results: 0/1 passed
  - 1 pipeline(s)
  - 0 prompts config(s)
  - 0 models config(s)

✗ 1 file(s) have errors

See: services/universal-stargate/systems/pipeline/README.md#v6-schema-specification
```

### Integration

**Pre-commit** (recommended):
```bash
# Add to .git/hooks/pre-commit
#!/bin/bash
python scripts/validate-pipeline.py pipelines.local/ || exit 1
```

**CI/CD**:
```yaml
# .github/workflows/validate.yml
- name: Validate Pipelines
  run: python scripts/validate-pipeline.py pipelines.local/
```

### See Also

- v6 Specification: `services/universal-stargate/systems/pipeline/README.md#v6-schema-specification`
- Pipeline validators: `services/universal-stargate/systems/pipeline/core/validation.py`
- Configuration validators: `scripts/validate-pipeline.py` (functions `validate_prompts_file`, `validate_models_file`)
- Schema: `services/universal-stargate/systems/pipeline/core/schemas.py`
- Common errors: `services/universal-stargate/systems/pipeline/EXTERNAL_WORKSPACE.md#validation-errors-and-fixes`
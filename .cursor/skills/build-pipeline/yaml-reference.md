# Pipeline YAML Reference

Thin agent-facing digest. **Stale when** `systems/pipeline/core/pipeline_config.py` or `core/step_config/` change (see `arch-docs-maintenance_ws`).

For bindings deep-dive: `QUICKSTART.md` §§1–7. For full v6 prose: `systems/pipeline/README.md`. Generated `docs/architecture/pipeline.md` is internals inventory only.

---

## Top-level (`PipelineSpec`)

| Field | Required | Notes |
|---|---|---|
| `schema_version` | yes | `6` for current authoring |
| `id` | yes | Registry / MCP `pipeline_id` |
| `version` | yes | e.g. `"1.0"` — omit on **sub-pipeline fragments** |
| `type` | yes | Domain key for handler routing |
| `steps` | yes | Ordered `StepConfig` list |
| `output` | yes | Terminal step name or binding |
| `options` | no | See `PipelineOptions` below |
| `output_format` | no | Only `json_array` when set |
| `token_defaults` | no | Category → max_tokens for steps without local max |
| `fragments` | no | Inline reusable step sequences |
| `checkpoint` | no | Checkpoint configuration |

**Sub-pipeline fragments** (`SubPipelineSpec`): `id` + `type` + `inputs` + `steps` + `output`. **No** `version` / `schema_version` (presence triggers standalone validation and breaks `optionsNs.*` / `inputs.*`).

---

## `options` (`PipelineOptions`)

Common explicit fields (extras allowed for domain keys):

| Field | Default | Notes |
|---|---|---|
| `timeout_seconds` | `60` | Must cover critical-path step timeouts |
| `include_alternates` | `false` | Blocks stream-passthrough eligibility |
| `include_step_stats` | `false` | Same |
| `max_tokens` | — | Pipeline-level ceiling |
| `disable_profile` / `profile` | — | Profile injection control |
| `save_execution_summary` | `false` | Disk execution log |
| `summary_format` | `markdown` | `markdown` \| `yaml` \| `json` \| `all` |

Domain extras (e.g. enrichment `assertion_id`, `claim`) land in `model_extra` and are visible as `optionsNs.*` / `context.options`.

---

## Step (`StepConfig`) — authoring fields

| Field | Role |
|---|---|
| `name` (YAML `id` alias) | Step identity |
| `type` | Built-in (`generate`, …) or domain step type |
| `handler_inputs` | `{field: binding}` — chat/generate data flow |
| `handler_outputs` | Declared outputs for downstream bindings |
| `output_declarations` | Typed output contracts (custom handlers) |
| `model_ref` / `prompt_ref` | Model alias + prompt namespace ref |
| `generation_parameters` | Temp, max_tokens, `response_format.schema` — **schema here, never in prompts.yaml** |
| `condition` | Skip/run predicate |
| `timeout_seconds` / `retry_policy` | Per-step limits |
| `map_config` | Map fan-out |
| `depends_on` | Prefer derived from `handler_inputs`; explicit only when needed |

Unknown YAML keys → domain fields via `extra="allow"`; read with `step.get_domain_field("…")`. Do **not** promote domain keys to first-class `StepConfig` attrs.

---

## Binding namespaces

| Namespace | Meaning |
|---|---|
| `sourceNs.text` | Request user input |
| `optionsNs.{key}` | Pipeline options (explicit + extras) |
| `{step}.json.{field}` / `{step}.text` | Prior step outputs |
| `loopNs.*` / `mapNs.*` | Loop / map iteration context only |

---

## Two authoring shapes

**Chat / generate** — `handler_inputs` from `sourceNs` + `prompt_ref` + `type: generate`. Exemplars: `pipelines/answer_v1/`, `pipelines/consensus/v8.0/`.

**Options-driven service** — little/no `handler_inputs`; handlers read `context.options`; custom step types + cortex writeback. Exemplars: `pipelines/assertion_enrichment/v1/`, `pipelines/predicate_extract/v1/`.

MCP: `pipeline(op="validate"|"run"|"async"|"result", pipeline_id=…)`.

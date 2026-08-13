<!-- target:* -->
# Capability Dispatch (Frontier Model Descriptors)

## What this is

**Invariant**: ∀ cloud/frontier model ids (`provider/model`, e.g. `openai/gpt-5.5`): the
typed **dispatch facet** is `CapabilityDispatch` — the authoritative description of which
API surface the model uses and which generation knobs it accepts.

| Term | Meaning |
|---|---|
| **`CapabilityDispatch`** | The descriptor (DATA) — use this name for the model card |
| **dispatch facet** | Informal synonym for `CapabilityDispatch` |
| **`ModelWrapper`** | Translation mechanism (MECHANISM) — NOT the descriptor |
| **`resolve_dispatch()`** | Runtime boundary that validates + resolves knobs before a provider call |

**¬** call the descriptor `ModelWrapper`. Wrapper = translator hydrated from dispatch.

## Model ID format

Cloud dispatch keys use the full admission id:

```
provider/model-id
```

Examples: `openai/gpt-5.5`, `anthropic/claude-sonnet-4-6`, `xai/grok-4.6`, `google/gemini-3-pro`

Bare aliases (`gpt-5.5`) resolve via `ModelId` + provider inference. Prefer `provider/model`
in specs, handoffs, and registry edits.

Related: cloud model routing (Stargate surfaces) — Cursor:
`universal-llm-gateway/.cursor/rules/cloud-model-routing_ws.mdc`.

## Descriptor schema (`CapabilityDispatch`)

```python
CapabilityDispatch(
    api_surface: str,           # anthropic | openai_responses | openai_chat_completions | google_generate_content
    max_output: CapabilityMaxOutput,
    reasoning: CapabilityReasoningDispatch | None,
    params: Mapping[str, KnobSpec],   # extensible extra knobs (often empty today)
    specializations: CapabilitySpecializations | None,
)
```

### `max_output` fields

| Field | Role |
|---|---|
| `default` | Used when caller omits `max_tokens` |
| `floor` | Minimum effective value (Responses: 16384) |
| `ceiling` | Hard upper bound (Anthropic per-family table) |
| `native_field` | Wire field name (`max_tokens`, `max_output_tokens`, `maxOutputTokens`) |
| `over_ceiling` | `clamp` or `reject` |

### `reasoning` fields

| `value_kind` | Provider behavior |
|---|---|
| `adaptive` | Anthropic adaptive thinking (`{"type":"adaptive"}`) |
| `token_budget` | Anthropic budget map (low/medium/high → token counts) |
| `effort_string` | OpenAI/xAI/Google (`{"effort": "high"}` or `thinkingConfig`) |

Accepted efforts: `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`.

### `KnobSpec` (`params` dict)

Optional declared knobs beyond max_output/reasoning. `default is OMIT` ⟺ leave absent
when caller omits the knob (never coerce to a wrong explicit default).

## Agent mental model — reasoning_effort is portable intent, not parity

`reasoning_effort` is a portable *intent* label, not cross-provider semantic parity.
The same string maps to different native shapes by `value_kind`: `effort_string` →
`{effort: e}`; `token_budget` → `{type: enabled, budget_tokens: N}` or, for efforts
absent from `budget_map`, no thinking; `adaptive` → `{type: adaptive}` with
`output_config.effort` assembled in gen_params.
To see the real per-dispatch resolution, read the `knob_resolution` /
`member_knob_resolution` echo on the dispatch envelope, or call `resolve_dispatch()`.
Never assume parity across providers.

## Provider affordances — workflow options beyond knobs

`CapabilityDispatch` describes the dispatch facet: surface, output limits, reasoning
translation, and accepted request knobs. It is not the whole provider capability
card. Some provider features change the workflow shape and must be surfaced as
**provider affordances** alongside the descriptor instead of being forced into
`team_dispatch` role language.

| Provider | Affordance class | Current agent-facing guidance |
|---|---|---|
| Anthropic | Advisor-style strategic checkpoint | Existing MCP `advisor` is a lightweight consult over caller-packaged context. Provider-native Anthropic advisor is a dispatch affordance candidate for Anthropic API calls: Sonnet/Haiku executor with Opus advisor inside one Messages request. It is not a `team_dispatch` role. |
| Anthropic | MCP client tools / server-side built-ins | Remote-connector vs client-side-loop selection is internal and card-derived — not a caller parameter. `mcp=` governs MCP-class tools; `server_tools=` governs card-derived provider built-ins independently. |
| Anthropic | Prompt/context controls | Adapter already exposes `context_management`, compact/fast betas, and output config through `provider_options.anthropic`; cost/latency behavior belongs in provider-affordance docs, not only knob validation. |
| OpenAI / xAI | Responses API state and reasoning | Responses requests use `reasoning.effort`, encrypted reasoning replay, `store=False`, and optional provider-native MCP for OpenAI. xAI remote MCP is currently rejected; xAI server-side built-ins are injected separately. |
| Google / Gemini | Thinking visibility and long-context behavior | Gemini uses `thinkingConfig` with `includeThoughts` when reasoning is requested; thought summaries are observable enough to support termination-shadow triage. Treat this as a workflow affordance, not just a reasoning knob. |

Agent rule: when an approach/session concern is mostly "is this the right path?",
consider the lightweight MCP `advisor` checkpoint before dispatching a full role
consult. Cursor seats can package transcript/tool evidence directly. Web seats must
construct a compact session concern bundle in `advisor.context` because the MCP
advisor cannot inspect the browser/chat transcript unless the caller includes it.
Use `team_dispatch` when the consult needs a named role, MCP tools, bus-thread
delivery, or durable handoff semantics. Use `panel_dispatch` when cross-family
disagreement is the point.

Audit target: maintain a provider-affordance card per API family (Anthropic,
OpenAI/xAI Responses, Google Gemini) so agents see overlooked provider-native
features before selecting a workflow. The card should answer: What can this
provider do that changes orchestration? Is it implemented? How is it enabled?
What are the cost/latency/accounting risks?

## Agent interface — lookup (read)

```python
from llm_adapters.capability_dispatch import resolve, resolve_dispatch, wrapper_for
from llm_adapters.capability_dispatch.serialization import to_wire_dict

# Descriptor only
dispatch = resolve("openai/gpt-5.5")

# Resolved values at the frontier boundary (what actually ships)
result = resolve_dispatch(
    "openai/gpt-5.5",
    requested_max_output=4096,
    reasoning_effort="high",
)
# result.max_output.resolved, result.max_output.decision, result.reasoning.native

# JSON-shaped facet (catalog / handoff payloads)
wire = to_wire_dict(dispatch)
```

Shell probe (no credentials):

```bash
python -c "from llm_adapters.capability_dispatch import resolve; from llm_adapters.capability_dispatch.serialization import to_wire_dict; import json; print(json.dumps(to_wire_dict(resolve('openai/gpt-5.5')), indent=2))"
```

## Agent interface — write (add/change a model)

**Source of truth**: `libs/llm_adapters/capability_dispatch/registry.py`

Gate checklist (required before merge):
`libs/llm_adapters/capability_dispatch/MODEL_ADD_CHECKLIST.md`

Lane A offline tests (every PR):

```bash
pytest libs/llm_adapters/test_dispatch_registry_coherence.py \
       libs/llm_adapters/test_max_output_parity.py -q
```

Lane B live probes (model-add): `scripts/dispatch-anti-drift/run.py`

## Runtime integration

| Layer | Role |
|---|---|
| `boundary.resolve_dispatch()` | Single frontier resolution site (G7) |
| `gen_params.build_frontier_request()` | Stargate callsite; adapters receive resolved `max_tokens` |
| `CapabilityDispatchFacet` | Pydantic mirror on gateway (`schemas/capabilities.py`) for `/v1/models` projection |

Events: `pipeline.frontier.dispatch.capability.{resolved,knob_rejected,catalog_miss}`

Errors:
- `ProtocolError` (G9) — unsupported knob; collect-all violations
- `CatalogMissError` (G13) — provider uninferable; fail-fast

## Representative models (quick reference)

| Model | `api_surface` | Notable dispatch |
|---|---|---|
| `openai/gpt-5.5` | `openai_responses` | floor 16384; effort_string; supports `reasoning.effort` |
| `xai/grok-4.6` | `openai_responses` | same surface; implicit default effort `high` |
| `anthropic/claude-sonnet-4-6` | `anthropic` | ceiling 64000; adaptive thinking |
| `google/gemini-3-pro` | `google_generate_content` | no floor/ceiling; `thinkingConfig` path |

## Handoff wording for other agents

When briefing agents on frontier models, use:

> Dispatch facet for `{model}`: `CapabilityDispatch` from `llm_adapters.capability_dispatch.resolve("{model}")`.
> Inspect via `to_wire_dict()` or `resolve_dispatch()` for resolved knob values.
> To add/change: edit `registry.py` per `MODEL_ADD_CHECKLIST.md`.

## Load triggers

Load this rule when: adding cloud models, debugging `reasoning_effort` / `max_tokens`
resolution, authoring frontier dispatch handlers, or writing specs that reference
model generation knobs.

**Read surface:** `docs/agent-guides/rules/capability-dispatch.md` (generated from this source).

```
fs(sandbox="workspaces", op="md_list",
   path="universal-llm-gateway/docs/agent-guides/rules/capability-dispatch.md")
fs(sandbox="workspaces", op="md_read",
   path="universal-llm-gateway/docs/agent-guides/rules/capability-dispatch.md",
   section="Agent interface — lookup (read)")
```
<!-- /target:* -->

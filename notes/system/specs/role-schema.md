# Cortex `role:{slug}` — execution contract schema (v1)

**Arc**: agent-naming cleanup — Phase 5  
**Decision**: `decision:persona-cleanup-revised-phasing-2026-05-11`  
**Design thread**: `agent-bus:953` (slug `role-schema-migration`)  
**Self-concept lint**: [role-schema-self-concept-lint.md](./role-schema-self-concept-lint.md)
**Canonical home**: git-tracked workspace path `notes/system/specs/` (not the Cortex sandbox)

## Pre-implementation confirmation (operator gate)

Operator resolutions from `tmp/prompts/agent-naming-cleanup-arc/README.md` (2026-05-12) are **accepted** for this wave:

1. Ship Phase 5 alongside Phase 1 — no renderer vs signature skew.
2. Cortex migration: **migrate-and-delete** for `ai_agent:` → `role:` (no `superseded_by` links).
3. Collective referent **team** retained (`team_dispatch`, etc.).

## Relationship to code registry

| Layer | Source | Responsibility |
|-------|--------|----------------|
| **Routing defaults** | [`config/agents.yaml`](../../../config/agents.yaml) `roles:` + `profiles:` | `RoleProfile` / `CapabilityProfile` via [`libs/agent_seat/profiles.py`](../../../libs/agent_seat/profiles.py) — default family, platform, model, allowlists, inline-only coercion. |
| **Execution contract** | Cortex `role:{slug}` entity | Purpose, tool gates, verification skills, failure modes, output contracts — third-person, execution-shaped only. |

**Resolution (Q1–Q2)**: **Augment** — YAML remains the canonical routing table loaded at process startup; Cortex `role:` entities carry the **extended** contract fields that belong in the knowledge graph (purpose prose, verification gates, operator-tunable failure/output policy). Dispatch continues to resolve models from YAML unless overridden by Cortex attributes the handler already reads (see hydration / frontier consult).

Post-implementation review on `agent-bus:953` ratified this split with one operator clarification: **roles are model-agnostic**. YAML supplies default assignments for omitted `model` values; explicit model overrides may fill any functional role. Provider or variant restrictions belong to concrete `(family, platform)` capability profiles, not to `role:{slug}` entities.

## Entity shape (Cortex)

```yaml
id: role:reviewer
type: role
name: Reviewer
description: >-
  Third-person roster label and one-line summary (mirrors YAML description;
  must pass self-concept lint).
attributes:
  purpose: |
    Third-person execution intent: structured review and audit with reliable
    MCP substrate writes when dispatched via API. Preferred human path is
    claude-web on agent-bus; automated default is openai/gpt-5.5; anthropic
    API models are last-resort overrides (cost).
  default_family: gpt
  default_platform: api
  default_model: openai/gpt-5.5
  allowed_models: [openai/gpt-5.5, anthropic/claude-sonnet-4-6, anthropic/claude-opus-4-8, google/gemini-3.1-pro-preview, google/gemini-2.5-pro]
  frontier_kind: openai
  required_tools: [cortex, fs]
  mcp_required: true
  verification:
    - skill: skill:named-entity-verification-gate
      hook: admit
  failure_mode:
    on_tool_unavailable: fail_closed
    on_model_unavailable: escalate_to_operator
    on_uncertainty: escalate_to_operator
    on_contract_violation: reject_dispatch
  output_schema:
    - markdown_response
    - optional_cortex_assertions_with_evidence_uris
```

### Field reference

| Field | Required | Notes |
|-------|----------|--------|
| `id` | yes | `role:{slug}`; slug matches YAML `roles` key. |
| `type` | yes | literal `role`. |
| `name` | yes | Short label; linted. |
| `description` | yes | Neutral summary; linted. |
| `attributes.purpose` | yes | Execution intent; linted. |
| `attributes.default_*` / `allowed_models` / `frontier_kind` | yes | Synced from YAML for default-model parity. These describe conventional fill-ins when `model` is omitted; they do not provider-lock the role when a caller supplies an explicit model. |
| `attributes.required_tools` | yes | Tool names (MCP catalog roots). Empty when inline-only. |
| `attributes.mcp_required` | yes | Minimum role contract. `false` means the role can operate on inline-only substrates; it does not suppress tools when a caller supplies an MCP-capable model. |
| `attributes.capability_tier` | no | Reserved for future role-intrinsic constraints. Do not mirror the default profile's `capability_tier` here; `inline-only` is a property of concrete seats/models, not of functional roles. |
| `attributes.required_model_substring` | no | Reserved for future role-intrinsic constraints. Do not mirror the default profile's `model_requirement` here; explicit model overrides may fill any role. |
| `attributes.verification` | no | List of `{skill, hook}` gates. `skill` is a `skill:{slug}` reference; `hook ∈ {admit, pre_emit, post_emit}` and defaults conceptually to `admit` when omitted by older payloads. |
| `attributes.failure_mode` | yes | Structured strings; linted recursively. |
| `attributes.output_schema` | yes | Flat list of dispatcher-readable contract strings; linted recursively. |
| `attributes.persona_seed_ref` | no | Reserved pointer; **not** linted. Unused on current `role:*` entities. |

## Worked example — `role:synthesizer`

`role:synthesizer` is the default analysis/synthesis role via the Gemini API profile
(`gemini/api`, `tool_surface: mcp`). Dispatches run the in-process MCP tool loop;
for guaranteed durable substrate writes prefer `reviewer` (gpt-5.5) or `claude-web`.
Default model is pro-tier for cognitive synthesis (Phase-B, agent-bus thread 1335).

```yaml
id: role:synthesizer
type: role
name: Synthesizer
description: Analysis/synthesis via MCP tools. Full catalog access; writes allowed by model discretion.
attributes:
  purpose: Analysis/synthesis via MCP tools. Full catalog access; writes allowed by model discretion. For guaranteed durable writes use reviewer (gpt-5.5) or claude-web.
  default_family: gemini
  default_platform: api
  default_model: google/gemini-3.1-pro-preview
  allowed_models:
    - google/gemini-3.1-pro-preview
    - google/gemini-3.5-flash
    - google/gemini-2.5-flash
    - google/gemini-2.5-pro
    - google/gemini-2.5-flash-lite
  frontier_kind: google
  required_tools: [cortex, fs, agent_bus]
  mcp_required: true
  verification: []
  failure_mode:
    on_tool_unavailable: fail_closed
    on_model_unavailable: escalate_to_operator
    on_uncertainty: escalate_to_operator
    on_contract_violation: reject_dispatch
  output_schema:
    - markdown_response
    - optional_cortex_assertions_with_evidence_uris
```

**Seat defaults** (`gemini/api`, `gemini/cursor`): throughput/lightweight work stays on
`google/gemini-3.5-flash` per `config/agents.yaml` `profiles:` — distinct from the
synthesizer role default above.

## Lint vocabulary (Q3)

Final predicate set = **R1–R4** in `role_lint` (see linked spec). Web review on thread **953** tightened R2 and requested an observed-vocabulary receipt for R3; changes require updating `libs/role_lint/__init__.py` + tests + this doc in one commit.

## Migration (complete — Phase 5E)

The `ai_agent:` → `role:` and birth-prompt retirement migration is **complete** on this workspace (operator gate 2026-05-12; Stargate `team_dispatch` / `frontier-dispatch` read `pipeline_options.role` only).

**Ongoing maintenance** (not a cutover checklist):

1. Upsert or refresh `role:{slug}` from [`scripts/cortex/sync_role_and_seat_entities.py`](../../../scripts/cortex/sync_role_and_seat_entities.py) when `config/agents.yaml` `roles:` changes. **Service restart alone does not update Cortex `role:*` attributes** — hydration reads `role:{slug}` first; run the sync script after YAML role edits (verified agent-bus thread 1335 post-Phase-B).
2. Run `role_lint` on any hand-edited `role:` payloads before `entity_create` / PATCH.
3. Grep the repo for vestigial `ai_agent:` / `prompt:*-birth` references in code paths (should be zero on dispatch surfaces).

Historical operator checklist (archived): [role-schema-migration-procedure.md](./role-schema-migration-procedure.md).

## Consult outcomes (async)

- **Web (lead)**: replied on thread **953** turns 3–5. Ratified the substrate and requested follow-up patches: align the spec to live payloads, tighten R2, add an R3 observed-vocabulary receipt, add verification hook semantics, add `on_model_unavailable`, and clarify that `role-schema-migration-procedure.md` lives in the git-tracked workspace path. The requested `role:skeptic` `capability_tier` lift was superseded by the operator invariant that any model can assume any role.
- **GPT-5.5 (reviewer)**: no reply observed on thread **953** as of the first continuation check after `transcript:cursor-2026-05-12-1619`; lint + tests remain the mechanical gate.

## Sync

Canonical script: [`scripts/cortex/sync_role_and_seat_entities.py`](../../../scripts/cortex/sync_role_and_seat_entities.py).

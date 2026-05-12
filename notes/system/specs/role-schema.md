# Cortex `role:{slug}` — execution contract schema (v1)

**Arc**: agent-naming cleanup — Phase 5  
**Decision**: `decision:persona-cleanup-revised-phasing-2026-05-11`  
**Design thread**: `agent-bus:953` (slug `role-schema-migration`)  
**Self-concept lint**: [role-schema-self-concept-lint.md](./role-schema-self-concept-lint.md)

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
    Third-person execution intent: what work this seat produces under what
    quality bar. No second-person address to the model.
  default_family: claude
  default_platform: api
  default_model: anthropic/claude-sonnet-4-6
  allowed_models: [anthropic/claude-sonnet-4-6, anthropic/claude-opus-4-7]
  frontier_kind: anthropic
  required_tools: [cortex, fs]
  mcp_required: true
  verification:
    - skill:named-entity-verification-gate
  failure_mode:
    on_tool_unavailable: fail_closed
    on_uncertainty: escalate_to_operator
    on_contract_violation: reject_dispatch
  output_schema:
    - markdown_findings
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
| `attributes.default_*` / `allowed_models` / `frontier_kind` | yes | Synced from YAML for parity; Stargate may read overrides via hydration. |
| `attributes.required_tools` | yes | Tool names (MCP catalog roots). Empty when inline-only. |
| `attributes.mcp_required` | yes | `false` for inline-only seats (e.g. skeptic / api-multi). |
| `attributes.verification` | no | List of `skill:{slug}` pre-ship gates. |
| `attributes.failure_mode` | yes | Structured strings; linted recursively. |
| `attributes.output_schema` | yes | List or structured manifest; linted recursively. |
| `attributes.persona_seed_ref` | no | Legacy only; **not** linted (pointer). Remove in Phase 6 retirement. |

## Lint vocabulary (Q3)

Final predicate set = **R1–R4** in `role_lint` (see linked spec). GPT / web async consults on thread **953** may extend the pattern list — changes require updating `libs/role_lint/__init__.py` + tests + this doc in one commit.

## Migration (Q4 + Phase 5E)

**Migrate-and-delete**:

1. For each legacy `ai_agent:{slug}` with a mapped functional role, distill execution-contract prose into `attributes.purpose` / `failure_mode` / `output_schema` (drop persona / birth / self-concept).
2. Upsert `role:{slug}` via `scripts/cortex/sync_role_and_seat_entities.py` (or `entity_create` with lint passing payload).
3. Remove hydration / dispatch code paths that still resolve `ai_agent:` for team dispatch (already complete in Stargate `role=` path — verify with repo grep).
4. **Delete** `ai_agent:{slug}` entities and `prompt:*-birth` entities once no live readers remain (operator or scripted DELETE when cortex API exposes hard-delete; until then use soft-retire + `workflow_state=superseded` per operator policy).

Detailed operator checklist: [role-schema-migration-procedure.md](./role-schema-migration-procedure.md).

## Consult outcomes (async)

- **Web (lead)**: `team_dispatch` issued for thread **953** (`execution_id` logged in `tmp/thread-state/953.md`). Reply may land after this commit; coherence **defaults** to the augment/YAML split above unless web posts a superseding turn.
- **GPT-5.5 (reviewer)**: same — dispatch optional; lint + tests are the mechanical gate.

## Sync

Canonical script: [`scripts/cortex/sync_role_and_seat_entities.py`](../../../scripts/cortex/sync_role_and_seat_entities.py) (replaces deprecated `sync-agent-identity` naming from older phase docs).

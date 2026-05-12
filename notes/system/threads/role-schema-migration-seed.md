# Phase 5 — Role schema migration (design thread seed)

**Arc**: `tmp/prompts/agent-naming-cleanup-arc/README.md`  
**Phase doc**: `tmp/prompts/agent-naming-cleanup-arc/phase-5-role-schema-migration.md`  
**Decision**: `decision:persona-cleanup-revised-phasing-2026-05-11`

## Context delta (substrate already shipped)

- `team_dispatch(op, role, ...)` is live in repo: `services/mcp-server/tools/frontier.py` → `/api/v1/team/dispatch`.
- `config/agents.yaml` + `libs/agent_seat/profiles.py` define `RoleProfile` (7 roles: lead, reviewer, gatherer, synthesizer, artisan, skeptic, investigator).

**Remaining Phase 5 work**: Cortex `role:{slug}` execution-contract entities, validation lint (reject self-concept language), migrate-and-delete from `ai_agent:`, sync script rewire. Phase 1 renderer strip aligns boot text with `role=` (no skew gap).

## Operator decisions (2026-05-12)

1. Ship Phase 5 alongside Phase 1 — no vocabulary skew window.
2. Cortex migration: migrate-and-delete; no `superseded_by` for `ai_agent:` → `role:`.
3. Keep collective "team" referents (`team_dispatch`, etc.).

## Schema v0 (for thread iteration)

| Field | Shape | Notes |
|-------|--------|------|
| `id` | `role:{slug}` | Slug matches `config/agents.yaml` `roles` key |
| `type` | `role` | Cortex entity type |
| `name` | string | Human label; not identity for the model |
| `purpose` | string (multiline) | Execution intent only; lint rejects self-concept phrasing |
| `allowed_models` | list[str] | Canonical gateway model IDs; may mirror YAML or be stricter |
| `required_tools` | list[str] | e.g. cortex, rag, fs, agent_bus |
| `mcp_required` | bool | |
| `verification` | list[str] | `skill:{slug}` gates |
| `failure_mode` | object | Keys: `on_tool_unavailable`, `on_uncertainty`, `on_contract_violation` |
| `output_schema` | list[str] or structured | Durable artifact contracts |

### Canonical YAML example (v0)

```yaml
id: role:reviewer
type: role
name: API code review
purpose: |
  Perform structured diff and subsystem review; surface defects with file:line
  evidence; do not assert ship readiness without enumerated checks.
allowed_models:
  - anthropic/claude-sonnet-4-6
  - anthropic/claude-opus-4-7
required_tools:
  - cortex
  - fs
mcp_required: true
verification:
  - skill:named-entity-verification-gate
failure_mode:
  on_tool_unavailable: fail_closed
  on_uncertainty: escalate_to_operator
  on_contract_violation: reject_dispatch
output_schema:
  - markdown_findings_or_empty
  - optional_cortex_assertion_evidence_uris
attributes:
  routing_profile: claude/api
```

`attributes.routing_profile` is optional metadata linking to `(family, platform)` for hydration; **not** self-concept text.

## Open design questions

1. **YAML vs Cortex**: Replace YAML role rows, augment (YAML = routing defaults, Cortex = contract prose + gates), or retire YAML for roles?
2. **`default_model` / `allowed_models`**: Single source in YAML vs Cortex vs split (recommended in plan: YAML for routing cells + role defaults; Cortex for narrative + verification).
3. **Lint vocabulary**: Finalize regex/predicate set for "you are", "your role is", "embody", "speak with the voice of", etc.
4. **Migration**: Which `ai_agent:*` assertion classes copy to `role:{slug}` vs retire (persona/birth fragments).

## Invites

- **Web seat**: coherence review on cross-agent protocol.
- **GPT-5.5** (via `team_dispatch`, `role=reviewer`, `model=openai/gpt-5.5`): execution-contract shape + lint sufficiency + persona-under-new-name regression check.

## Deliverable

Final spec path (cortex sandbox): `notes/system/specs/role-schema.md`

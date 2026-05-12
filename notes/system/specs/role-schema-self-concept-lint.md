# Role entity — self-concept lint (R1–R4)

**Normative source**: [`libs/role_lint/__init__.py`](../../../libs/role_lint/__init__.py)

## Purpose

Rejects identity-coded prose on `role:{slug}` Cortex entities so execution contracts cannot smuggle persona framing under a new name (Phase 5, agent-naming cleanup arc).

## Linted surfaces

| Path | Rules |
|------|--------|
| `name` | R1–R4 |
| `description` | R1–R4 |
| `attributes.purpose` | R1–R4 |
| `attributes.required_model_substring` | R1–R4 |
| `attributes.failure_mode` (recursive) | R1–R4 |
| `attributes.output_schema` (recursive) | R1–R4 |

`persona_seed_ref` URIs are **not** string-walked for pattern hits (legacy pointer only).

## Rule classes

- **R1** — second-person identity assertion (`you are`, `your role is`, …).
- **R2** — voice / embodiment construction (`speak as`, `embodies the perspective of`, …).
- **R3** — metaphor-as-identity (`I am the`, `The aperture —`, …).
- **R4** — weak collective signal (`our team`, …) — **warning** only; does not fail `lint_role_payload`.

## Enforcement points

1. **Cortex API** — `create_entity_impl` / `update_entity_impl` reject `type=role` payloads that fail R1–R3 (`422` with `role_lint_rejected`).
2. **Sync script** — [`scripts/cortex/sync_role_and_seat_entities.py`](../../../scripts/cortex/sync_role_and_seat_entities.py) runs the same lint before upsert.

## Tests

[`libs/role_lint/test_role_lint.py`](../../../libs/role_lint/test_role_lint.py)

# `ai_agent:` → `role:` migration procedure (operator)

**Policy**: migrate-and-delete — no `superseded_by` edges from `ai_agent:` to `role:`.

## Preconditions

- [`role-schema.md`](./role-schema.md) v1 approved.
- `python scripts/cortex/sync_role_and_seat_entities.py --dry-run` passes (lint + payload shape).

## Steps

1. **Canary** — run sync without `--dry-run` against dev cortex socket; verify one `team_dispatch(role=reviewer, …)` consult succeeds.
2. **Bulk upsert** — run full sync; confirm seven `role:*` entities in cortex-ui or `entity_get`.
3. **Stop loading birth prompts** — confirm `libs/agent_seat/hydration.py` / frontier consult paths do not fetch `prompt:*-birth` for team roles (grep clean).
4. **Retire `ai_agent:`** — for each legacy entity: export assertions if legally required, then DELETE or soft-retire per cortex capability. Hard-delete may require DB operator if API omits delete.
5. **Retire `prompt:*-birth`** — delete files / entities in Phase 6 sweep once grep shows zero consumers.
6. **Assertion** — seed `decision:persona-cleanup-revised-phasing-2026-05-11` with `evidence_uris` including commit SHA and `agent-bus:953`.

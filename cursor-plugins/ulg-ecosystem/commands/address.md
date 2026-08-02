Address — settled work-item lifecycle peer beside bundled `/path-sim`.

**SOT:** consult-routing § Address (bind_status chooser). This file is a thin When/Invocation wrapper — ¬ path-sim L0/L1/L2 cascade tables.

## When

| Condition | Route |
|---|---|
| `bind_status=settled` or `shipping` ∧ `density_triage≠recon_pending` | This command — advance/verify/close |
| `bind_status=unsettled` ∧ `density_triage∈{judgment_required,recon_pending}` | `/path-sim` (bundled) — not this lane |
| `bind_status=deferred` | held — `next_action=await_unblock`; no route |
| Gate-2 `implement_ready` just stamped | Writer sets `bind_status=settled`; pickup here after densify |
| Operator `/address todo:{slug}` or `/address a:{id}` | This command |

## Invocation

```
/address todo:{slug}
/address a:{assertion_id}
```

## Lead obligations (binding)

1. Load `consult-routing` § Address — bind_status chooser is authoritative.
2. `entity_get(todo:{slug}, intent="full")` — confirm four card keys (`workflow`, `stage`, `bind_status`, `next_action`) on durable attrs.
3. Advance lifecycle via `entity_update` + `merge_state_card` (`libs/cortex_store/dispatch_ops/state_card.py`) when writing `stage` or `bind_status` (e.g. `settled`→`shipping`, stage `pickup`→`advance`→`verify`→`close`).
4. Ship/verify per todo dense spec; do not re-run path-sim cascade for settled binds.
5. Close with workflow_state + evidence per `implement-todo` §5.

SOT: consult-routing § Address

## Skills

Use the `consult-routing` skill · `implement-todo` skill · `entity-lifecycle-discipline` skill

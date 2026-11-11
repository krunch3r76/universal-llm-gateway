"""One-shot reconcile for pending activation validations on terminal restart intents."""

from __future__ import annotations

import json

from charter_runner_store.propagation_validation import (
    get_validation,
    pending_validations,
    reconcile_pending_validations_for_terminal_intents,
)


def main() -> int:
    from scripts.model_manager.ui.controller.restart_intent_store import (
        RestartIntentStore,
    )

    store = RestartIntentStore()
    pending_before = [
        {
            "validation_id": v.validation_id,
            "service": v.service,
            "outcome": v.outcome,
            "restart_intent": v.restart_intent,
        }
        for v in pending_validations()
    ]
    touched = reconcile_pending_validations_for_terminal_intents(store=store)
    pending_after = [
        {
            "validation_id": v.validation_id,
            "service": v.service,
            "outcome": v.outcome,
            "restart_intent": v.restart_intent,
        }
        for v in pending_validations()
    ]
    for vid in touched:
        row = get_validation(vid)
        if row is not None:
            print(
                json.dumps(
                    {
                        "validation_id": vid,
                        "outcome": row.outcome,
                        "code_ref_relation": row.code_ref_relation,
                    }
                )
            )
    print(
        json.dumps(
            {
                "pending_before_count": len(pending_before),
                "pending_after_count": len(pending_after),
                "reconciled_validation_ids": touched,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Compact propagate terminal payloads — spill oversized proof blobs inline."""

from __future__ import annotations

import json
from typing import Any

# Keep small proofs inline; large served-artifact maps move to ``proof_spill``.
_PROOF_INLINE_CHAR_LIMIT = 1_500


def compact_propagate_terminal_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Move oversized ``executions[]`` proof maps to ``proof_spill`` with refs.

    Full spill content remains in the terminal JSON so bus soft auto-spill
    (``allow_long_body=false``) preserves evidence in a cortex sidecar rather
    than dropping it to a hard 413.
    """
    executions = payload.get("executions")
    if not isinstance(executions, list):
        return payload

    spill: dict[str, Any] = {}
    compact_execs: list[Any] = []
    for item in executions:
        if not isinstance(item, dict):
            compact_execs.append(item)
            continue
        compact = dict(item)
        row_id = str(compact.get("row_id") or len(spill))
        for field in ("proof", "proof_before", "proof_at_submit"):
            value = compact.pop(field, None)
            if value is None:
                continue
            encoded = json.dumps(value, separators=(",", ":"), sort_keys=True)
            if len(encoded) <= _PROOF_INLINE_CHAR_LIMIT:
                compact[field] = value
                continue
            spill_key = f"{row_id}.{field}"
            spill[spill_key] = value
            compact[field] = {
                "spilled": True,
                "spill_key": spill_key,
                "chars": len(encoded),
            }
        compact_execs.append(compact)

    out = dict(payload)
    out["executions"] = compact_execs
    if spill:
        out["proof_spill"] = spill
        out["proof_spill_note"] = (
            "Large proof artifacts are in proof_spill. When the terminal JSON "
            "exceeds the bus soft inline limit the store auto-spills the full "
            "payload (including proof_spill) to a cortex sidecar."
        )
    return out


__all__ = ["compact_propagate_terminal_payload", "_PROOF_INLINE_CHAR_LIMIT"]

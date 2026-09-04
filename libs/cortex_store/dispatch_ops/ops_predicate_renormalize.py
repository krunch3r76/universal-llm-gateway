"""Dispatch op — T0 predicate-normalize mechanical adjudicator."""

from __future__ import annotations

from typing import Any

from ..db import cortex_conn
from ..renormalize import dry_run_stratify, t0_adjudicate_flagged
from ._shared import record


def _op_predicate_renormalize(
    dry_run: bool = True,
    sample_limit: int = 50,
    **_: object,
) -> dict[str, Any]:
    """Run dry_run_stratify and optional T0 flag-fields-only clear pass."""
    with cortex_conn() as conn:
        stratify = dry_run_stratify(conn)
        if dry_run:
            return {
                "dry_run": True,
                "stratify_count": len(stratify),
                "stratify_snapshot": stratify,
            }
        result = t0_adjudicate_flagged(
            conn,
            dry_run=False,
            sample_limit=sample_limit,
            record_event=record,
        )
        result["dry_run"] = False
        return result


__all__ = ["_op_predicate_renormalize"]

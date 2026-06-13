"""Campaign reporting for delivery-audit token-locality baseline traces."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from .delivery_audit_baseline_types import (
    P95_CAVEAT_THRESHOLD,
    SUMMARY_TOKEN_FIELDS,
    VALID_PHASES,
    VALID_SEAT_SUBSTRATES,
    VALID_WORKFLOW_CLASSES,
    require_known,
)
from .delivery_audit_registry import connect


def fetch_workflow_summaries(
    *,
    campaign_id: str,
    phase: str | None = None,
    workflow_class: str | None = None,
    seat_substrate: str | None = None,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Return workflow-summary rows matching a campaign slice."""
    clauses = ["campaign_id = ?"]
    params: list[Any] = [campaign_id]
    for column, value, valid in (
        ("phase", phase, VALID_PHASES),
        ("workflow_class", workflow_class, VALID_WORKFLOW_CLASSES),
        ("seat_substrate", seat_substrate, VALID_SEAT_SUBSTRATES),
    ):
        if value is None:
            continue
        require_known(value, valid, column)
        clauses.append(f"{column} = ?")
        params.append(value)
    query = f"""
        SELECT * FROM guidance_workflow_summaries
        WHERE {" AND ".join(clauses)}
        ORDER BY workflow_class, seat_substrate, input_tokens, execution_id
    """
    with connect(db_path) as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


def _nearest_rank_index(size: int, percentile: float) -> int:
    return max(0, min(size - 1, math.ceil(percentile * size) - 1))


def _category_vector(row: dict[str, Any]) -> dict[str, int]:
    return {field: int(row[field]) for field in SUMMARY_TOKEN_FIELDS}


def _summarize_group(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: (row["input_tokens"], row["execution_id"]))
    p50_row = ordered[_nearest_rank_index(len(ordered), 0.50)]
    p95_row = ordered[_nearest_rank_index(len(ordered), 0.95)]
    sample_count = len(ordered)
    return {
        "sample_count": sample_count,
        "meets_milestone1_minimum": sample_count >= 30,
        "p50_input_tokens": p50_row["input_tokens"],
        "p95_input_tokens": p95_row["input_tokens"],
        "p50_token_vector": _category_vector(p50_row),
        "p95_token_vector": _category_vector(p95_row),
        "p95_caveat": (
            "wide_interval_n_lt_50" if sample_count < P95_CAVEAT_THRESHOLD else None
        ),
    }


def summarize_baseline_campaign(
    campaign_id: str,
    *,
    phase: str = "baseline",
    seat_substrate: str | None = None,
    workflow_class: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Report campaign p50/p95 token-locality statistics by workflow class."""
    rows = fetch_workflow_summaries(
        campaign_id=campaign_id,
        phase=phase,
        workflow_class=workflow_class,
        seat_substrate=seat_substrate,
        db_path=db_path,
    )
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (row["workflow_class"], row["seat_substrate"])
        groups.setdefault(key, []).append(row)

    summaries = [
        {
            "workflow_class": workflow,
            "seat_substrate": seat,
            **_summarize_group(group_rows),
        }
        for (workflow, seat), group_rows in sorted(groups.items())
    ]
    return {
        "campaign_id": campaign_id,
        "phase": phase,
        "workflow_class": workflow_class,
        "seat_substrate": seat_substrate,
        "workflow_summaries": summaries,
        "workflow_group_count": len(summaries),
        "trace_count": len(rows),
    }

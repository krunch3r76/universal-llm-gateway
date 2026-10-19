"""Monotonic conductor hop_seq resolution shared by hop and park-harvest."""

from __future__ import annotations

from typing import Any

from services.git_integration_worker.cursor_sdk_ledger_hop import hop_fields_from_record_json


def resolve_next_hop_seq(
    *,
    ledger_max_hop_seq: int | None,
    prior_hop_seq: int | None,
    closeout_hop_seq: int | None,
) -> int | None:
    """Return successor hop_seq or None when no input resolves."""
    candidates = [
        x
        for x in (ledger_max_hop_seq, prior_hop_seq, closeout_hop_seq)
        if isinstance(x, int)
    ]
    if not candidates:
        return None
    return max(candidates) + 1


def ledger_max_hop_seq_for_thread(rows: list[dict[str, Any]]) -> int | None:
    """Max ``hop_seq`` observed on ledger rows for one worker thread."""
    best: int | None = None
    for row in rows:
        hop_fields = hop_fields_from_record_json(str(row.get("record_json") or ""))
        hop_seq = hop_fields.get("hop_seq")
        if isinstance(hop_seq, int):
            best = hop_seq if best is None else max(best, hop_seq)
    return best


def resolve_next_hop_seq_from_row(
    row: dict[str, Any],
    rec: dict[str, Any],
    *,
    ledger_rows: list[dict[str, Any]] | None = None,
) -> int | None:
    """Combine ledger, record hop_fields, and closeout hop_seq."""
    closeout_raw = rec.get("closeout_hop_seq")
    closeout = int(closeout_raw) if isinstance(closeout_raw, int) else None
    hop_fields = hop_fields_from_record_json(str(row.get("record_json") or ""))
    prior_raw = hop_fields.get("hop_seq")
    prior = int(prior_raw) if isinstance(prior_raw, int) else None
    ledger_max = (
        ledger_max_hop_seq_for_thread(ledger_rows) if ledger_rows is not None else None
    )
    return resolve_next_hop_seq(
        ledger_max_hop_seq=ledger_max,
        prior_hop_seq=prior,
        closeout_hop_seq=closeout,
    )

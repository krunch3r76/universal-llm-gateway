"""Ledger-evidenced CONSULT_QUEUED wedge detection (6486/6563 heal path)."""

from __future__ import annotations

import json
from typing import Any

from ..consult_lane import _load_queue_row
from ..root_ledger import RootLedgerRow, RootStatus
from ..work_key import compute_work_key
from ..work_key_store import harvested_for_key

HEAL_REASON = "consult_work_key_harvested"


def _env_facts(row: RootLedgerRow) -> dict[str, Any]:
    raw = row.env_facts_json
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _consult_work_key_candidates(
    row: RootLedgerRow,
    *,
    consult_role: str,
    facts: dict[str, Any],
) -> list[str]:
    """Work-key identities to probe — ledger + env_facts stamps only (no tip parse)."""
    candidates: list[str] = []
    stamped = facts.get("consult_work_key")
    if isinstance(stamped, str) and stamped.strip():
        candidates.append(stamped.strip())
    last_harvested = facts.get("last_harvested_work_key")
    if isinstance(last_harvested, str) and last_harvested.strip():
        candidates.append(last_harvested.strip())

    source_refs: list[str | None] = [None]
    source_ref = facts.get("consult_source_ref")
    if isinstance(source_ref, str) and source_ref.strip():
        source_refs.insert(0, source_ref.strip())

    for source in source_refs:
        candidates.append(
            compute_work_key(
                root_id=row.root_id,
                source_ref=source,
                pickup_gid=row.pickup_gid,
                consult_role=consult_role,
                admission_mode="consult",
                pickup_lane=row.pickup_lane,
            )
        )
    # Preserve order, drop dupes.
    return list(dict.fromkeys(candidates))


def consult_work_key_is_harvested(
    conn,
    row: RootLedgerRow,
    consult_role: str,
) -> bool:
    """True when consult_queue is active and work_key registry shows harvested.

    Disposition is read from ``charter_window_work_key`` only — never from tip
    executor tokens (6486/6576 bind).
    """
    if row.status != RootStatus.CONSULT_QUEUED:
        return False
    gid = row.pickup_gid or "G?"
    queue = _load_queue_row(conn, row.root_id, gid, consult_role)
    if queue is None or queue.status != "queued":
        return False

    facts = _env_facts(row)
    for key in _consult_work_key_candidates(row, consult_role=consult_role, facts=facts):
        if harvested_for_key(conn, key):
            return True
    # ¬ sole-harvested-row fallback: a prior window's harvested key on this root
    # (e.g. W1/G3) must not heal a later gid's fresh CONSULT_QUEUED (6563 G4
    # live wedge — QUEUE→HEAL loop for ~1h).
    return False


__all__ = ["HEAL_REASON", "consult_work_key_is_harvested"]

"""Join-key helpers — map ``cdp.generate.*`` events to hop-cadence succession claims.

Stargate ``execution_id`` is available at hop commission; ``satellite_execution_id``
arrives on ``cdp.generate.submitted``. Either key may appear on ``stalled``.
"""

from __future__ import annotations

from typing import Any


def normalize_id(value: Any) -> str | None:
    """Return a stripped non-empty id string, else None."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def claim_join_keys(row: dict[str, Any]) -> frozenset[str]:
    """Execution ids held on a watch row's pending/active succession claim."""
    keys: set[str] = set()
    pending = row.get("pending_succession")
    if isinstance(pending, dict):
        for field in ("execution_id", "satellite_execution_id"):
            ident = normalize_id(pending.get(field))
            if ident:
                keys.add(ident)
    for field in (
        "successor_execution_id",
        "last_hop_execution_id",
        "pending_execution_id",
        "pending_satellite_execution_id",
    ):
        ident = normalize_id(row.get(field))
        if ident:
            keys.add(ident)
    return frozenset(keys)


def event_join_keys(payload: dict[str, Any]) -> frozenset[str]:
    """Join keys extracted from a ``cdp.generate.*`` event payload."""
    keys: set[str] = set()
    for field in ("execution_id", "satellite_execution_id", "request_id"):
        ident = normalize_id(payload.get(field))
        if ident:
            keys.add(ident)
    top = normalize_id(payload.get("execution_id"))
    if top:
        keys.add(top)
    return frozenset(keys)


def proof_observes_harvest(payload: dict[str, Any]) -> bool:
    """True when a proof event carries an observed harvest URI, not just an id.

    ``cdp.generate.proof`` may repeat a Stargate ``execution_id`` minted at
    admit. Successor-produced confirmation requires an archive or content
    proof URI — the observation that generate actually harvested.
    """
    archive = normalize_id(payload.get("archive_uri"))
    content = normalize_id(payload.get("content_proof_uri"))
    return bool(archive or content)


def stall_matches_claim(
    stall_payload: dict[str, Any],
    row: dict[str, Any],
) -> bool:
    """True when a stalled event payload joins to this watch's succession claim."""
    if row.get("succession_status") != "pending":
        return False
    stall_keys = event_join_keys(stall_payload)
    claim_keys = claim_join_keys(row)
    if not stall_keys or not claim_keys:
        return False
    return bool(stall_keys & claim_keys)


def submitted_updates_claim(
    submit_payload: dict[str, Any],
    row: dict[str, Any],
) -> str | None:
    """Return satellite_execution_id to attach when submit joins a pending claim."""
    if row.get("succession_status") != "pending":
        return None
    exec_id = normalize_id(submit_payload.get("execution_id"))
    pending = row.get("pending_succession")
    if not isinstance(pending, dict):
        return None
    claim_exec = normalize_id(pending.get("execution_id"))
    if not exec_id or not claim_exec or exec_id != claim_exec:
        return None
    return normalize_id(submit_payload.get("satellite_execution_id"))


__all__ = [
    "claim_join_keys",
    "event_join_keys",
    "normalize_id",
    "proof_observes_harvest",
    "stall_matches_claim",
    "submitted_updates_claim",
]

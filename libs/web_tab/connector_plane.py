"""Server-stored connector observation for one house and agent.

A historical attestation is last-observed evidence. It does not prove the
connector is alive right now, and principal equality is not house proof.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from typing import Any

from universal_protocol.status_basis import status_basis


def principal_matches_attested_turn(
    authenticated_principal: str | None,
    attested_by: str | None,
) -> bool | None:
    """Exact equality of two canonical principal strings.

    Either side missing is unknown (``None``). A prefix, suffix, or shared
    credential overlap is not a match, and a match is not house or tab proof.
    """
    if not isinstance(authenticated_principal, str) or not isinstance(attested_by, str):
        return None
    left = authenticated_principal.strip()
    right = attested_by.strip()
    if not left or not right:
        return None
    return left == right


def _attestation_age_s(created_at: object, *, now_dt: datetime) -> float | None:
    if not isinstance(created_at, str) or not created_at.strip():
        return None
    text = created_at.strip()
    try:
        stamp = datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None
    return max(0.0, (now_dt - stamp).total_seconds())


def default_attestation_reader(house_id: str, agent: str) -> dict[str, Any]:
    """Read the latest stored attestation without creating a database.

    ``available`` is false when the bus file is missing, the store cannot be
    imported, or sqlite/OS access fails. A present database with no qualifying
    row is available and ``turn`` is ``None``.
    """
    try:
        from agent_bus_store.db.connection import _db_path
        from agent_bus_store.db.turns import get_latest_attested_turn
    except ImportError:
        return {"available": False, "turn": None, "reason": "import"}
    path = _db_path()
    if not os.path.isfile(path):
        return {"available": False, "turn": None, "reason": "missing_db"}
    try:
        turn = get_latest_attested_turn(house_id, agent)
    except (sqlite3.Error, OSError):
        return {"available": False, "turn": None, "reason": "bus_unavailable"}
    return {"available": True, "turn": turn, "reason": None}


def observe_connector_plane_from_server(
    *,
    house_id: str,
    agent: str,
    attestation: dict[str, Any],
    authenticated_principal: str | None = None,
) -> dict[str, Any]:
    """Report last-observed attestation. Current connector liveness stays unknown.

    *attestation* is the server read (``available`` / ``turn``). Request option
    fields are not accepted here. *authenticated_principal* is compared to
    ``attested_by`` with exact equality and labeled
    ``principal_matches_attested_turn``. A historical row does not set
    ``connector_alive``.
    """
    now_dt = datetime.now(tz=UTC)
    now = now_dt.isoformat()
    scope = f"house:{house_id}/agent:{agent}"
    available = bool(attestation.get("available"))
    turn = (
        attestation.get("turn")
        if available and isinstance(attestation.get("turn"), dict)
        else None
    )
    if not available:
        source = "bus.unavailable"
        turn_no = None
        attested_by = None
        evidence_as_of = now
        age = None
    elif turn is None:
        source = "bus.attestation"
        turn_no = None
        attested_by = None
        evidence_as_of = now
        age = None
    else:
        source = "bus.attestation"
        raw_no = turn.get("turn_number")
        turn_no = int(raw_no) if isinstance(raw_no, int) else None
        raw_by = turn.get("attested_by")
        attested_by = (
            str(raw_by).strip() if isinstance(raw_by, str) and raw_by.strip() else None
        )
        created = turn.get("created_at")
        evidence_as_of = str(created) if isinstance(created, str) and created else now
        age = _attestation_age_s(created, now_dt=now_dt)
    match = principal_matches_attested_turn(authenticated_principal, attested_by)
    return {
        "plane": "connector",
        "house_id": house_id,
        "agent": agent,
        "connector_alive": status_basis(
            "connector_alive",
            None,
            as_of=now,
            source="connector.unobserved",
            scope=scope,
            epoch={"reason": "attestation_is_not_current_liveness"},
        ),
        "last_attested_turn": status_basis(
            "last_attested_turn",
            turn_no,
            as_of=evidence_as_of,
            source=source,
            scope=scope,
            epoch={
                "attested_by": attested_by,
                "attestation_age_s": age,
                "from_agent": agent,
            },
        ),
        "request_identity": authenticated_principal,
        "principal_matches_attested_turn": match,
    }

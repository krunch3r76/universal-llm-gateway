"""Skeptic evidence grounding for doc_validate (aligned with Stargate dispatch)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from transport_utils import DEFAULT_AGENT_BUS_URL, make_sync_client

from .ops_assertions import _op_assertions
from .ops_implement_ready_preflight import _normalize_predicate

_AGENT_BUS_TIMEOUT = 15.0


class DispatchSkepticBusReader:
    def bus_turn_get(self, thread: str, turn_number: int) -> dict[str, Any] | None:
        from urllib.parse import urlencode

        token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        qs = urlencode({"thread": str(thread), "turn_number": int(turn_number)})
        with make_sync_client(
            DEFAULT_AGENT_BUS_URL, timeout=_AGENT_BUS_TIMEOUT
        ) as client:
            resp = client.get(f"/turns/by-number?{qs}", headers=headers)
            if resp.status_code >= 400:
                return None
            data = resp.json()
        return data if isinstance(data, dict) else None

    def bus_thread_last_turn(self, thread: str) -> dict[str, Any] | None:
        from urllib.parse import urlencode

        token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        qs = urlencode({"thread": str(thread), "last": 1})
        with make_sync_client(
            DEFAULT_AGENT_BUS_URL, timeout=_AGENT_BUS_TIMEOUT
        ) as client:
            resp = client.get(f"/turns?{qs}", headers=headers)
            if resp.status_code >= 400:
                return None
            payload = resp.json()
        if not isinstance(payload, dict):
            return None
        turns = payload.get("turns")
        if not isinstance(turns, list) or not turns:
            return None
        first = turns[0]
        return first if isinstance(first, dict) else None


def find_skeptic_assertion(
    *,
    todo_id: str,
    spec_hash_uri: str,
    now_iso: str,
) -> dict[str, Any] | None:
    listed = _op_assertions(
        entity_id=todo_id,
        confidence="confirmed",
        superseded=False,
        intent="full",
        limit=50,
    )
    items = listed.get("items") if isinstance(listed, dict) else None
    if not isinstance(items, list):
        return None
    target = _normalize_predicate(f"status({todo_id}, skeptic_ratified, current)")
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("entity_id") != todo_id:
            continue
        if _normalize_predicate(item.get("predicate_form")) != target:
            continue
        valid_until = item.get("valid_until")
        if item.get("superseded_by") or (valid_until and str(valid_until) <= now_iso):
            continue
        evidence = item.get("evidence_uris")
        if isinstance(evidence, list) and spec_hash_uri in evidence:
            return item
    return None


def evaluate_skeptic_grounding(
    *,
    skeptic_assertion: dict[str, Any] | None,
    ws_root: Path,
) -> dict[str, Any]:
    if skeptic_assertion is None:
        return {
            "ratified": False,
            "evidence_grounded": None,
            "evidence_unresolved": None,
            "evidence_mode": None,
            "deferred_to_stargate": False,
        }
    try:
        from systems.frontier_consult.skeptic_evidence_grounding import (
            evaluate_skeptic_evidence_grounding,
        )
    except ImportError:
        return {
            "ratified": True,
            "evidence_grounded": None,
            "evidence_unresolved": None,
            "evidence_mode": None,
            "deferred_to_stargate": True,
        }
    try:
        outcome = evaluate_skeptic_evidence_grounding(
            reader=DispatchSkepticBusReader(),
            assertion=skeptic_assertion,
            workspaces_root=ws_root,
        )
    except Exception:
        return {
            "ratified": True,
            "evidence_grounded": None,
            "evidence_unresolved": None,
            "evidence_mode": None,
            "deferred_to_stargate": True,
        }
    return {
        "ratified": True,
        "evidence_grounded": outcome.grounded,
        "evidence_unresolved": outcome.unresolved,
        "evidence_mode": outcome.mode,
        "deferred_to_stargate": False,
    }

"""Public provenance read — compose registry resolution without collapsing claim/proven."""

from __future__ import annotations

from typing import Any

from claude_bundles import cdp_registry
from claude_bundles.cse_provenance import resolve as resolve_provenance
from claude_bundles.cse_provenance_resolve import is_host_listable
from claude_bundles.cse_url import normalize_cse_url

from cdp_ask.cse_session_events import (
    emit,
    mcp_cse_session_conflict,
    mcp_cse_session_resolved,
)
from cdp_ask.cse_session_models import ProvenanceQuery, ProvenanceResponse
from cdp_ask.execution_store import ExecutionStore


def _execution_id_for_registration(
    execution_id: str | None,
    store: ExecutionStore | None,
) -> str | None:
    """Return execution_id only when the live store supplies it — never invent."""
    if not execution_id or store is None:
        return None
    return execution_id


async def _execution_registration(
    execution_id: str,
    store: ExecutionStore | None,
) -> str | None:
    if store is None:
        return None
    record = await store.get(execution_id)
    if record is None:
        return None
    return record.registration_id


def self_supersession(
    predecessor_registration_id: str | None,
    successor_registration_id: str | None,
    *,
    execution_id: str | None = None,
    successor_execution_id: str | None = None,
) -> bool:
    pred = (predecessor_registration_id or "").strip()
    succ = (successor_registration_id or "").strip()
    if pred and succ and pred == succ:
        return True
    exec_a = (execution_id or "").strip()
    exec_b = (successor_execution_id or "").strip()
    return bool(exec_a and exec_b and exec_a == exec_b)


def _list_candidates() -> list[dict[str, Any]]:
    """Return one candidate row per active or dormant registration with provenance."""
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for lane in cdp_registry.list_active():
        if lane.registration_id in seen:
            continue
        seen.add(lane.registration_id)
        bound = cdp_registry.chat_url_for_registration(lane.registration_id)
        prov = resolve_provenance(
            chat_url=bound,
            registration_id=lane.registration_id,
            host_listable=is_host_listable,
        )
        rows.append(
            {
                "registration_id": lane.registration_id,
                "chat_url": bound,
                "holder": lane.holder,
                "purpose": lane.purpose,
                "provenance": prov,
            }
        )
    for seat in cdp_registry.list_dormant():
        if seat.registration_id in seen:
            continue
        seen.add(seat.registration_id)
        prov = resolve_provenance(
            chat_url=seat.chat_url,
            registration_id=seat.registration_id,
            host_listable=is_host_listable,
        )
        rows.append(
            {
                "registration_id": seat.registration_id,
                "chat_url": seat.chat_url,
                "holder": seat.holder,
                "purpose": seat.purpose,
                "dormant": True,
                "provenance": prov,
            }
        )
    return rows


def _project(raw: dict[str, Any], *, execution_id: str | None = None) -> ProvenanceResponse:
    payload = dict(raw)
    if execution_id:
        payload["execution_id"] = execution_id
    elif "execution_id" in payload:
        payload.pop("execution_id", None)
    return ProvenanceResponse(**{k: v for k, v in payload.items() if k in ProvenanceResponse.model_fields})


async def resolve_public_provenance(
    query: ProvenanceQuery,
    *,
    store: ExecutionStore | None = None,
) -> ProvenanceResponse | dict[str, Any]:
    """Resolve provenance or return typed conflict / candidate list."""
    if self_supersession(
        query.predecessor_registration_id,
        query.successor_registration_id,
        execution_id=query.execution_id,
    ):
        emit(
            mcp_cse_session_conflict(
                reason="self_supersession",
                registration_id=query.successor_registration_id,
                chat_url=query.chat_url,
            )
        )
        return ProvenanceResponse(
            state="conflict",
            reason="self_supersession",
            chat_url=normalize_cse_url(query.chat_url or "") or query.chat_url,
            registration_id=query.successor_registration_id,
        )

    chat_url = (query.chat_url or "").strip() or None
    registration_id = (query.registration_id or "").strip() or None
    execution_id = (query.execution_id or "").strip() or None

    if execution_id and not registration_id:
        registration_id = await _execution_registration(execution_id, store)

    if not any((chat_url, registration_id, execution_id)):
        return {"candidates": _list_candidates()}

    raw = resolve_provenance(
        chat_url=chat_url,
        registration_id=registration_id,
        host_listable=is_host_listable,
    )
    resolved = _project(
        raw,
        execution_id=_execution_id_for_registration(execution_id, store),
    )
    emit(
        mcp_cse_session_resolved(
            registration_id=resolved.registration_id,
            chat_url=resolved.chat_url,
            state=resolved.state,
        )
    )
    return resolved

"""Public provenance read — compose registry resolution without collapsing claim/proven."""

from __future__ import annotations

from typing import Any

from claude_bundles import cdp_registry
from claude_bundles.cse_provenance import resolve as resolve_provenance
from claude_bundles.cse_provenance_resolve import is_row_present
from claude_bundles.cse_url import normalize_cse_url

from cdp_ask.cse_session_events import (
    emit,
    mcp_cse_session_conflict,
    mcp_cse_session_resolved,
)
from cdp_ask.cse_session_models import ProvenanceQuery, ProvenanceResponse
from cdp_ask.execution_store import ExecutionStore


def _registry_projection(registration_id: str | None) -> dict[str, Any]:
    """Authoritative registry fields for provenance (Jupiter local store)."""
    reg = (registration_id or "").strip()
    if not reg:
        return {}
    try:
        from claude_bundles import cdp_registry_store as registry_store
        from claude_bundles.cdp_registry.models import seat_open
        from claude_bundles.cdp_registry.session_address import (
            chat_url_for_registration,
        )
    except Exception:
        return {}
    active = registry_store.load_active()
    row = active.get(reg) if isinstance(active, dict) else None
    if not isinstance(row, dict):
        return {}
    bound = (
        chat_url_for_registration(reg) or row.get("chat_url") or ""
    ).strip() or None
    return {
        "purpose": row.get("purpose"),
        "status": row.get("status"),
        "seat_open": seat_open(row),
        "chat_url": bound,
    }


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
            host_listable=is_row_present,
        )
        row_extra = _registry_projection(lane.registration_id)
        rows.append(
            {
                "registration_id": lane.registration_id,
                "chat_url": bound,
                "holder": lane.holder,
                "purpose": lane.purpose,
                "provenance": prov,
                **{k: v for k, v in row_extra.items() if k != "chat_url" or not bound},
            }
        )
    for seat in cdp_registry.list_dormant():
        if seat.registration_id in seen:
            continue
        seen.add(seat.registration_id)
        prov = resolve_provenance(
            chat_url=seat.chat_url,
            registration_id=seat.registration_id,
            host_listable=is_row_present,
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


def _project(
    raw: dict[str, Any], *, execution_id: str | None = None
) -> ProvenanceResponse:
    payload = dict(raw)
    if execution_id:
        payload["execution_id"] = execution_id
    elif "execution_id" in payload:
        payload.pop("execution_id", None)
    return ProvenanceResponse(
        **{k: v for k, v in payload.items() if k in ProvenanceResponse.model_fields}
    )


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
        host_listable=is_row_present,
    )
    resolved = _project(
        raw,
        execution_id=_execution_id_for_registration(execution_id, store),
    )
    reg_for_projection = (
        (resolved.registration_id or "").strip()
        or (registration_id or "").strip()
        or None
    )
    enrich = _registry_projection(reg_for_projection)
    if enrich:
        merged = {**resolved.model_dump(), **enrich}
        queried_reg = (registration_id or "").strip()
        if queried_reg and not str(merged.get("registration_id") or "").strip():
            merged["registration_id"] = queried_reg
        resolved = ProvenanceResponse(
            **{k: v for k, v in merged.items() if k in ProvenanceResponse.model_fields}
        )
    elif registration_id and not str(resolved.registration_id or "").strip():
        resolved = resolved.model_copy(update={"registration_id": registration_id})
    emit(
        mcp_cse_session_resolved(
            registration_id=resolved.registration_id,
            chat_url=resolved.chat_url,
            state=resolved.state,
        )
    )
    return resolved

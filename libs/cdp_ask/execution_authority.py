"""Read-side assembly of registry authority and store projection for one execution."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from claude_bundles.cdp_registry import execution_state as reg_state


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


async def read_execution_authority(
    *,
    execution_id: str,
    satellite_execution_id: str | None,
    execution_store: Any,
) -> dict[str, Any]:
    """Read registry authority and store projection without poll recovery."""
    lookup_ids = [execution_id]
    if satellite_execution_id and satellite_execution_id not in lookup_ids:
        lookup_ids.append(satellite_execution_id)

    sources: list[dict[str, Any]] = []
    authority_entry: dict[str, Any] | None = None
    authority_reg: str | None = None
    freshness: str | None = None
    for eid in lookup_ids:
        hit = reg_state.execution_state_for_execution_id(eid)
        if hit is not None:
            authority_reg, authority_entry, freshness = hit
            sources.append(
                {
                    "source": "cdp_registry.execution_state",
                    "hit": True,
                    "execution_id": eid,
                }
            )
            break
        sources.append(
            {
                "source": "cdp_registry.execution_state",
                "hit": False,
                "execution_id": eid,
                "reason": "no_row",
            }
        )

    store_payload: dict[str, Any] | None = None
    for eid in lookup_ids:
        record = await execution_store.get(eid)
        if record is None:
            sources.append(
                {
                    "source": "cdp_ask.execution_store",
                    "hit": False,
                    "execution_id": eid,
                    "reason": "miss",
                }
            )
            continue
        store_payload = {
            "execution_id": record.execution_id,
            "status": record.status,
            "registration_id": record.registration_id,
            "streaming": getattr(record, "streaming", None),
        }
        sources.append(
            {
                "source": "cdp_ask.execution_store",
                "hit": True,
                "execution_id": eid,
            }
        )
        break

    return {
        "execution_state": authority_entry,
        "execution_state_freshness": freshness,
        "registration_id": authority_reg,
        "store": store_payload,
        "sources_consulted": sources,
        "as_of": _utc_now_iso(),
    }

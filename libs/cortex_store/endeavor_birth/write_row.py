"""Named write_row / dispose_row service surface (F-M2)."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status

from ..db import cortex_conn, json_encode
from .constants import DISPOSITION_SET, ROW_PREDICATE, VISIBILITY_SET
from .events import (
    cortex_endeavor_row_disposed,
    cortex_endeavor_row_pending,
    cortex_endeavor_strategy_pin_missing,
)
from .lock_model import lock_ready
from .read_models import host_entity_row
from .strategy_row import find_live_row, pending, validate_disposition

# routes.assertions → close_draft → ops_audit_detectors; import lazily to avoid cycle
# when endeavor_birth package is loaded from audit detectors.

def _closed_shape_error(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"error": "endeavor_strategy_row_shape_invalid", "message": message},
    )


def validate_row_fields(fields: dict[str, Any]) -> dict[str, Any]:
    row_id = fields.get("row_id")
    if not row_id or not str(row_id).strip():
        raise _closed_shape_error("row_id is required")
    disposition = fields.get("disposition")
    if disposition == "pending":
        raise _closed_shape_error("pending is not a disposition; use disposition=null")
    if disposition is not None and disposition not in DISPOSITION_SET:
        raise _closed_shape_error(f"invalid disposition {disposition!r}")
    if disposition is None or disposition == "omit_with_reason":
        if not fields.get("reason"):
            raise _closed_shape_error("reason is required when disposition is null or omit_with_reason")
    visibility = fields.get("visibility")
    if visibility is not None and visibility not in VISIBILITY_SET:
        raise _closed_shape_error(f"invalid visibility {visibility!r}")
    affects = fields.get("affects")
    if not affects or not isinstance(affects, list) or not affects:
        raise _closed_shape_error("affects must be a non-empty list of deliverable ids")
    if not fields.get("authority"):
        raise _closed_shape_error("authority is required")
    return {
        "row_id": str(row_id),
        "theme": fields.get("theme"),
        "material": bool(fields.get("material", False)),
        "disposition": disposition,
        "reason": fields.get("reason"),
        "bounds": fields.get("bounds"),
        "visibility": visibility or "internal",
        "authority": str(fields["authority"]),
        "affects": [str(v) for v in affects],
        "pin": fields.get("pin"),
    }


def write_row(
    host: str,
    fields: dict[str, Any],
) -> dict[str, Any]:
    from ..routes.assertions._create import _create_assertion_impl

    with cortex_conn() as conn:
        host_row = host_entity_row(conn, host)
        if host_row is None:
            raise HTTPException(status_code=404, detail=f"host not found: {host}")
        validated = validate_row_fields(fields)
        if find_live_row(conn, host, validated["row_id"]) is not None:
            raise _closed_shape_error(
                f"non-superseded row already exists for ({host}, {validated['row_id']})"
            )
        attrs = {k: v for k, v in validated.items() if k != "pin"}
        claim = (
            f"Strategy row {validated['row_id']} on {host}: "
            f"material={validated['material']} disposition={validated['disposition']!r}"
        )
        body: dict[str, Any] = {
            "entity_id": host,
            "claim": claim,
            "confidence": "believed",
            "evidence": "endeavor strategy row write_row",
            "derivation_type": "agent_observation",
            "predicate_form": f"{ROW_PREDICATE}({host}, {validated['row_id']})",
            "attributes": attrs,
        }
        if validated["material"] and validated["disposition"] is None:
            body["resolution_status"] = "pending"
            body["review_status"] = "flagged"
        result = _create_assertion_impl(body)
        item = result.get("item") or {}
        assertion_id = int(item["id"])
        pin_id = assertion_id
        if validated["material"] and validated["disposition"] is None:
            attrs["pin"] = pin_id
            conn.execute(
                "UPDATE assertions SET attributes = ? WHERE id = ?",
                (json_encode(attrs), assertion_id),
            )
            conn.commit()
            cortex_endeavor_strategy_pin_missing(host=host, row_id=validated["row_id"])
            cortex_endeavor_row_pending(host=host, row_id=validated["row_id"], pin=pin_id)
        ready, blocking = lock_ready(conn, host, validated["affects"][0])
        return {
            "host": host,
            "row_id": validated["row_id"],
            "assertion_id": assertion_id,
            "pin": pin_id,
            "lock_ready": ready,
            "blocking_rows": blocking,
            "assertion": result,
        }


def dispose_row(
    host: str,
    row_id: str,
    disposition: str,
    reason: str | None = None,
    authority: str | None = None,
) -> dict[str, Any]:
    from ..routes.assertions._supersede import _supersede_assertion_impl

    validate_disposition(disposition)
    with cortex_conn() as conn:
        live = find_live_row(conn, host, row_id)
        if live is None:
            raise HTTPException(status_code=404, detail=f"no live row for ({host}, {row_id})")
        if not pending(live):
            raise _closed_shape_error(f"row {row_id!r} is not pending")
        attrs = {
            "row_id": live.row_id,
            "theme": live.theme,
            "material": live.material,
            "disposition": disposition,
            "reason": reason or live.reason,
            "bounds": live.bounds,
            "visibility": live.visibility or "internal",
            "authority": authority or live.authority,
            "affects": list(live.affects),
            "pin": live.pin,
        }
        claim = f"Strategy row {row_id} on {host} disposed as {disposition}"
        body = {
            "entity_id": host,
            "old_assertion_id": live.assertion_id,
            "claim": claim,
            "confidence": "believed",
            "evidence": "endeavor strategy row dispose_row",
            "derivation_type": "agent_observation",
            "predicate_form": f"{ROW_PREDICATE}({host}, {row_id})",
            "attributes": attrs,
            "force": True,
        }
        result = _supersede_assertion_impl(body)
        new_item = result.get("new") or {}
        new_id = int(new_item["id"])
        cortex_endeavor_row_disposed(host=host, row_id=row_id, pin=live.pin or new_id)
        deliverable = live.affects[0] if live.affects else ""
        ready, blocking = (True, [])
        if deliverable:
            ready, blocking = lock_ready(conn, host, deliverable)
        return {
            "host": host,
            "row_id": row_id,
            "superseded_assertion_id": live.assertion_id,
            "disposing_assertion_id": new_id,
            "lock_ready": ready,
            "blocking_rows": blocking,
            "assertion": result,
        }

"""Per-type required-attribute schema lookup + validation.

Sibling to ``workflow_state.py``: where workflow_schemas governs the
typed lifecycle column, type_attribute_schemas governs the
required-attribute contract for entities of that type. Both registries
are seeded by migrations and read at write time during ``entity_create``.

Per docs/architecture/entity-backed-claim-provenance.md § 1.1 / § 1.2 /
§ 1.3 / § 4.1, types like ``legal_source``, ``case-law``, ``exhibit``,
and ``brief`` carry structural-attribute contracts that must be enforced
at write time so the structural-gap detector (§ 7) can rely on the
attributes being present. Types without a registered schema accept any
attributes (free-form), preserving backwards-compatibility with the
broader Cortex graph.
"""

from __future__ import annotations

import json
import sqlite3

from fastapi import HTTPException, status
from universal_logging import get_logger

from .db import query, table_exists

logger = get_logger("cortex-api.type_schemas")


def type_attribute_schema(
    conn: sqlite3.Connection, entity_type: str
) -> dict[str, object] | None:
    """Fetch the attribute schema for *entity_type* if registered, else None.

    Returns None either when no row exists for *entity_type* OR when the
    ``type_attribute_schemas`` table itself is absent. The latter happens
    in test sandboxes that pre-date migration 037 and in production
    instances where migrations have not yet been applied — graceful
    degradation matches the existing ``workflow_state.workflow_schema``
    contract: types not registered accept any attributes (free-form).
    """
    if not table_exists(conn, "type_attribute_schemas"):
        return None
    rows = query(
        conn,
        "SELECT required_keys, optional_keys, enum_constraints, notes "
        "FROM type_attribute_schemas WHERE entity_type = ?",
        (entity_type,),
    )
    if not rows:
        return None
    row = rows[0]
    return {
        "required": json.loads(row["required_keys"]),
        "optional": json.loads(row["optional_keys"]),
        "enums": json.loads(row["enum_constraints"]),
        "notes": row["notes"],
    }


def validate_required_attributes(
    conn: sqlite3.Connection,
    entity_type: str,
    attributes: dict[str, object] | None,
) -> None:
    """Reject entity_create when required attributes are absent or invalid.

    Validation rules:
      1. Every key in ``schema["required"]`` MUST appear in ``attributes``.
      2. Any key constrained by ``schema["enums"]`` (whether required or
         optional) MUST hold a value in the registered allow-list when
         present. Absent enum-constrained keys are allowed unless the key
         is also required.

    Types not registered in ``type_attribute_schemas`` skip validation
    entirely (free-form attributes).
    """
    schema = type_attribute_schema(conn, entity_type)
    if schema is None:
        return

    attrs = attributes or {}
    required = schema["required"]
    enums = schema["enums"]
    assert isinstance(required, list)
    assert isinstance(enums, dict)

    missing = [key for key in required if key not in attrs]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "type_attribute_required_missing",
                "entity_type": entity_type,
                "missing": missing,
                "required": required,
            },
        )

    enum_violations: list[dict[str, object]] = []
    for key, allowed in enums.items():
        if key not in attrs:
            continue
        value = attrs[key]
        if value not in allowed:
            enum_violations.append(
                {"attribute": key, "value": value, "allowed": allowed}
            )

    if enum_violations:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "type_attribute_enum_violation",
                "entity_type": entity_type,
                "violations": enum_violations,
            },
        )


_IMPLEMENT_LANE_KEYS = ("files_expected", "acceptance_criteria", "required_skills")
_DEPRECATED_IMPLEMENT_ALIASES = {
    "files_modified": "files_expected",
    "acceptance": "acceptance_criteria",
}


def _implement_lane_shape_invalid(key: str, value: object) -> bool:
    if not isinstance(value, list) or not value:
        return True
    return not all(isinstance(item, str) and item.strip() for item in value)


def validate_distilled_attributes(
    conn: sqlite3.Connection,
    entity_type: str,
    attributes: dict[str, object] | None,
) -> None:
    """Stricter-than-materializer shape gate for implement-lane attrs.

    Runs only on supplied implement-lane keys. Rejects (422):
      - deprecated aliases (files_modified / acceptance) — post-059 typos;
      - implement-lane key present but not a non-empty list[str] of non-empty strings;
      - implement-lane key not registered in the type's schema (when the type is registered).
    Other attribute keys (priority, domain, density_triage, ...) are untouched.
    """
    attrs = attributes or {}
    present_lane_keys = [key for key in _IMPLEMENT_LANE_KEYS if key in attrs]
    if not present_lane_keys and not any(k in attrs for k in _DEPRECATED_IMPLEMENT_ALIASES):
        return

    for alias, canonical in _DEPRECATED_IMPLEMENT_ALIASES.items():
        if alias in attrs:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "implement_attr_alias_rejected",
                    "entity_type": entity_type,
                    "alias": alias,
                    "canonical": canonical,
                },
            )

    schema = type_attribute_schema(conn, entity_type)
    registered: set[str] | None = None
    if schema is not None:
        registered = set(schema["required"]) | set(schema["optional"])
        assert isinstance(registered, set)

    for key in present_lane_keys:
        if registered is not None and key not in registered:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "implement_attr_not_registered",
                    "entity_type": entity_type,
                    "attribute": key,
                },
            )
        if _implement_lane_shape_invalid(key, attrs[key]):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "implement_attr_shape_invalid",
                    "entity_type": entity_type,
                    "attribute": key,
                },
            )


__all__ = [
    "type_attribute_schema",
    "validate_distilled_attributes",
    "validate_required_attributes",
]

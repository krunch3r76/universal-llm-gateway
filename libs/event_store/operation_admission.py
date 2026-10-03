"""Authoritative param admission at the Event Service HTTP boundary."""

from __future__ import annotations

from typing import Any

from .operation_catalog import get_operation

_SQL_PARAMS: dict[str, dict[str, Any]] = {
    "sql": {"type": "string", "required": True},
    "params": {"type": "list"},
    "limit": {"type": "int", "default": 100},
}


def _declared_for(member: str) -> dict[str, dict[str, Any]] | None:
    if member == "sql":
        return _SQL_PARAMS
    op = get_operation(member)
    if op is None:
        return None
    return dict(op.params)


def admit(member: str, params: dict[str, Any]) -> dict[str, Any] | None:
    """Return an error dict when params fail presence, unknown-key, or shape checks."""
    declared = _declared_for(member)
    if declared is None:
        return None
    unknown = sorted(key for key in params if key not in declared)
    missing = sorted(
        key
        for key, meta in declared.items()
        if meta.get("required") and key not in params
    )
    invalid: list[str] = []
    for key, value in params.items():
        if key not in declared:
            continue
        kind = declared[key].get("type", "string")
        if kind == "int":
            if isinstance(value, bool) or not _int_ok(value):
                invalid.append(key)
        elif kind == "list":
            if not isinstance(value, list):
                invalid.append(key)
        elif kind == "string" and not isinstance(value, str):
            invalid.append(key)
    if not unknown and not missing and not invalid:
        return None
    return {
        "code": "INVALID_PARAMS",
        "message": f"Invalid params for {member}",
        "source": "rpc",
        "retryable": False,
        "data": {
            "member": member,
            "declared": sorted(declared),
            "unknown": unknown,
            "missing": missing,
            "invalid": invalid,
        },
    }


def _int_ok(value: Any) -> bool:
    if isinstance(value, int):
        return True
    if isinstance(value, str):
        try:
            int(value, 10)
        except ValueError:
            return False
        return True
    return False

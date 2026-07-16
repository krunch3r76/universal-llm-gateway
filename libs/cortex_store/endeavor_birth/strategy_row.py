"""Strategy-row substrate — assertion-as-row on host (F-M1)."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from ..db import query
from .constants import DISPOSITION_SET, ROW_PREDICATE

_ROW_PRED_RE = re.compile(
    rf"^{ROW_PREDICATE}\(([^,]+),\s*([^)]+)\)$"
)


@dataclass(frozen=True)
class StrategyRow:
    assertion_id: int
    host: str
    row_id: str
    theme: str | None
    material: bool
    disposition: str | None
    reason: str | None
    bounds: str | None
    visibility: str | None
    authority: str | None
    affects: tuple[str, ...]
    pin: int | None
    resolution_status: str | None


def _parse_attrs(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}


def _parse_affects(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, list):
        return tuple(str(v) for v in value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return tuple(str(v) for v in parsed)
        except json.JSONDecodeError:
            pass
        return (value,)
    return (str(value),)


def parse_row(row: dict[str, Any]) -> StrategyRow | None:
    predicate = (row.get("predicate_form") or "").strip()
    match = _ROW_PRED_RE.match(predicate)
    if not match:
        return None
    host = match.group(1).strip()
    row_id = match.group(2).strip().strip("'\"")
    attrs = _parse_attrs(row.get("attributes"))
    pin_raw = attrs.get("pin")
    pin = int(pin_raw) if pin_raw is not None else None
    return StrategyRow(
        assertion_id=int(row["id"]),
        host=host,
        row_id=row_id,
        theme=attrs.get("theme"),
        material=bool(attrs.get("material")),
        disposition=attrs.get("disposition"),
        reason=attrs.get("reason"),
        bounds=attrs.get("bounds"),
        visibility=attrs.get("visibility"),
        authority=attrs.get("authority"),
        affects=_parse_affects(attrs.get("affects")),
        pin=pin,
        resolution_status=row.get("resolution_status"),
    )


def rows(conn: sqlite3.Connection, host: str) -> list[StrategyRow]:
    db_rows = query(
        conn,
        "SELECT id, predicate_form, attributes, resolution_status "
        "FROM assertions WHERE entity_id = ? AND superseded_by IS NULL "
        "AND predicate_form LIKE ?",
        (host, f"{ROW_PREDICATE}(%"),
    )
    parsed: list[StrategyRow] = []
    for row in db_rows:
        item = parse_row(row)
        if item is not None:
            parsed.append(item)
    return parsed


def pending(row: StrategyRow) -> bool:
    return row.disposition is None


def live_pin(conn: sqlite3.Connection, pin_id: int, row_id: str) -> bool:
    pin_rows = query(
        conn,
        "SELECT id, predicate_form, resolution_status FROM assertions "
        "WHERE id = ? AND superseded_by IS NULL",
        (pin_id,),
    )
    if not pin_rows:
        return False
    pin_row = pin_rows[0]
    if pin_row.get("resolution_status") != "pending":
        return False
    parsed = parse_row(pin_row)
    return parsed is not None and parsed.row_id == row_id


def pin_ok(conn: sqlite3.Connection, row: StrategyRow) -> bool:
    if not pending(row):
        return True
    if row.pin is None:
        return False
    return live_pin(conn, row.pin, row.row_id)


def find_live_row(conn: sqlite3.Connection, host: str, row_id: str) -> StrategyRow | None:
    for item in rows(conn, host):
        if item.row_id == row_id:
            return item
    return None


def validate_disposition(value: str | None) -> str | None:
    if value is None:
        return None
    if value == "pending":
        raise ValueError("pending is not a disposition value; use disposition=null")
    if value not in DISPOSITION_SET:
        raise ValueError(f"invalid disposition {value!r}")
    return value

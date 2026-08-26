"""Mint policy for referent entity types (Fork 2 — opaque ULID slug + mutable name)."""

from __future__ import annotations

import os
import re
import sqlite3
import time

from fastapi import HTTPException, status

MINTED_TYPES = frozenset({"person", "organization"})

_MINTED_SLUG_RE = re.compile(r"^[0-9a-hjkmnp-tv-z]{26}$")
_CROCKFORD = "0123456789abcdefghjkmnpqrstvwxyz"

_LIVE_LIFECYCLE_SQL = (
    "(entities.lifecycle IS NULL"
    " OR entities.lifecycle NOT IN ('merged','deprecated','reaped'))"
)


def is_minted_type(entity_type: str) -> bool:
    return entity_type in MINTED_TYPES


def is_minted_local_slug(slug: str) -> bool:
    """True when *slug* is a 26-char Crockford ULID local segment."""
    return bool(_MINTED_SLUG_RE.match(slug))


def mint_ulid_slug() -> str:
    """Return a 26-char lowercase Crockford ULID local slug."""
    timestamp_ms = int(time.time() * 1000)
    ulid_bytes = timestamp_ms.to_bytes(6, "big") + os.urandom(10)
    value = int.from_bytes(ulid_bytes, "big")
    chars: list[str] = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def mint_entity_id(entity_type: str) -> str:
    if entity_type not in MINTED_TYPES:
        raise ValueError(f"entity type {entity_type!r} is not minted")
    return f"{entity_type}:{mint_ulid_slug()}"


def reject_minted_type_id_supplied(
    entity_type: str,
    supplied_id: str,
    *,
    caller: str = "entity_create",
) -> None:
    """Raise 422 when *id* is supplied for a minted type (B2)."""
    from .event_publisher import cortex_entity_create_id_rejected

    cortex_entity_create_id_rejected(
        entity_type=entity_type,
        supplied_id=supplied_id,
        caller=caller,
    )
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "code": "entity_id_minted_type",
            "message": (
                f"Entity type {entity_type!r} uses server-minted ids — omit id "
                "and supply the display string in name."
            ),
            "source": caller,
            "retryable": False,
            "data": {
                "fix": f"omit id; set name=<display string> for type={entity_type}",
            },
        },
    )


def check_duplicate_name(
    conn: sqlite3.Connection,
    *,
    entity_type: str,
    name: str,
    duplicate_name_ok: bool = False,
) -> None:
    """Raise 409 when a live entity already owns the (type, name) alias row (B5)."""
    if duplicate_name_ok or entity_type not in MINTED_TYPES:
        return
    try:
        row = conn.execute(
            f"""
            SELECT ea.entity_id
            FROM entity_aliases ea
            JOIN entities ON entities.id = ea.entity_id
            WHERE ea.entity_type = ? AND ea.alias = ?
              AND {_LIVE_LIFECYCLE_SQL}
            LIMIT 1
            """,
            (entity_type, name),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table: entity_aliases" in str(exc):
            return
        raise
    if row is None:
        return
    existing_id = str(row[0])
    from .event_publisher import cortex_entity_name_duplicate_rejected

    cortex_entity_name_duplicate_rejected(
        entity_type=entity_type,
        name=name,
        existing_entity_id=existing_id,
    )
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "entity_name_exists",
            "message": (
                f"An entity of type {entity_type!r} with name {name!r} already exists."
            ),
            "source": "entity_create",
            "retryable": False,
            "data": {
                "existing_entity_id": existing_id,
                "hint": (
                    "update/merge the existing entity or pass duplicate_name_ok=true"
                ),
            },
        },
    )

"""TTL-cached projection of error_code → agent_skill hints from graph relationships."""

from __future__ import annotations

import time
from typing import Any

from .db import cortex_conn, query
from .guidance_entity import GUIDANCE_ID_PREFIXES, entity_slug_from_id
from .relationship_sql import FROM_CLAUSE, SELECT_COLUMNS

_CACHE_TTL_S = 30.0

_cache: dict[str, Any] = {"loaded_at": 0.0, "values": {}}


def clear_skill_hint_cache() -> None:
    """Test hook — bust the relationship-projection TTL cache."""
    _cache["loaded_at"] = 0.0
    _cache["values"] = {}


def _slug_to_code(slug: str) -> str:
    return slug.replace("-", "_")


def _load_projection_values() -> dict[str, str]:
    now = time.monotonic()
    if now - float(_cache["loaded_at"]) < _CACHE_TTL_S and _cache["values"]:
        return dict(_cache["values"])

    values: dict[str, str] = {}
    try:
        with cortex_conn() as conn:
            rows = query(
                conn,
                f"SELECT {SELECT_COLUMNS} {FROM_CLAUSE} "
                "WHERE r.active = 1 AND r.type = 'references' "
                "AND r.from_entity LIKE 'error_code:%' "
                "ORDER BY r.created_at DESC",
                (),
            )
        for row in rows:
            source_id = str(row.get("source_id") or "")
            target_id = str(row.get("target_id") or "")
            if not source_id.startswith("error_code:"):
                continue
            if not any(target_id.startswith(prefix) for prefix in GUIDANCE_ID_PREFIXES):
                continue
            code = _slug_to_code(source_id.removeprefix("error_code:"))
            skill_slug = entity_slug_from_id(target_id)
            if code and skill_slug:
                values.setdefault(code, skill_slug)
    except Exception:
        values = {}

    _cache["loaded_at"] = now
    _cache["values"] = values
    return dict(values)


def get_skill_hint(error_code: str) -> str | None:
    """Resolve a tool error code to an agent_skill entity id, if seeded."""
    code = error_code.strip()
    if not code:
        return None
    skill_slug = _load_projection_values().get(code)
    if not skill_slug:
        return None
    return f"agent_skill:{skill_slug}"


__all__ = ["clear_skill_hint_cache", "get_skill_hint"]

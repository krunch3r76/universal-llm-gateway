"""Display-:8 UUID subscription row enumeration and H1 selection."""

from __future__ import annotations

import re
from typing import Any

_HOUSELESS_DISPLAYS = frozenset({"8", ":8"})
_UUID_AGENT_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I,
)
_TAB_URL_UUID_RE = re.compile(
    r"cursor://(?:tab|agent)/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b",
    re.I,
)


def parse_tab_uuid(tab_url: str | None) -> str | None:
    text = str(tab_url or "").strip()
    if not text:
        return None
    match = _TAB_URL_UUID_RE.search(text)
    if match:
        return match.group(1).lower()
    return None


def is_uuid_agent_id(value: object) -> str | None:
    text = str(value or "").strip()
    if _UUID_AGENT_RE.match(text):
        return text.lower()
    return None


def enumerate_display8_uuid_rows(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("surface") or "") != "cursor":
            continue
        disp = str(row.get("display") or "").strip()
        if disp not in _HOUSELESS_DISPLAYS:
            continue
        if str(row.get("state") or "") != "subscribed":
            continue
        uid = is_uuid_agent_id(row.get("agent_id"))
        if uid is None:
            continue
        out.append(row)
    return out


def h1_rows_for_scope(
    rows: list[dict[str, Any]], scope: str
) -> list[dict[str, Any]]:
    """H1: MAX(id) subscribed display-:8 UUID row per (agent, thread_scope)."""
    scope_s = str(scope or "").strip()
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for row in enumerate_display8_uuid_rows(rows):
        if str(row.get("thread_scope") or "").strip() != scope_s:
            continue
        agent = str(row.get("agent") or "").strip()
        key = (agent, scope_s)
        row_id = int(row.get("id") or 0)
        prev = best.get(key)
        if prev is None or row_id > int(prev.get("id") or 0):
            best[key] = row
    return list(best.values())


__all__ = [
    "enumerate_display8_uuid_rows",
    "h1_rows_for_scope",
    "is_uuid_agent_id",
    "parse_tab_uuid",
]

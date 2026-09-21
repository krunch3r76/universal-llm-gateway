"""ROW_CLASS latch — CDP binds low|trio on the house thread; ticker fires the path.

CDP row-bind posts ``ROW_CLASS: low|trio`` (+ optional ``ROW_WHY``, ``ROW_ID``)
then stops. The ticker parses that line from root turns, stores
``state.row_class[a:n]={class, why, row_id, at}``, and on the next spawn fires
LOW (implement lane B) or TRIO (play conductor or CDP sketch) — never grok
house generate on a forcing friction sit leftover.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from bus_watch.spawn_wake.play_classify import LEFTOVER_SIT, extract_todo_slug

ROW_CLASS_LOW = "low"
ROW_CLASS_TRIO = "trio"
ROW_CLASS_HOLD = "row_class_hold"
ROW_CLASS_FIRED = "row_class_fired"
_KEEP = 200

_ROW_CLASS_RE = re.compile(r"^ROW_CLASS:\s*(low|trio)\s*$", re.I | re.M)
_ROW_WHY_RE = re.compile(r"^ROW_WHY:\s*(.+?)\s*$", re.M)
_ROW_ID_RE = re.compile(r"^ROW_ID:\s*(.+?)\s*$", re.M)
_AMBIGUOUS_CLASS_RE = re.compile(r"^ROW_CLASS:\s*(.+?)\s*$", re.I | re.M)


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def undispositioned_forcing_friction(digest: dict[str, Any]) -> dict[str, Any] | None:
    """Newest forcing friction score row still awaiting disposition."""
    rows = list(digest.get("frictions") or [])
    for row in rows:
        if not row.get("forcing"):
            continue
        if row.get("state") == "close_pending":
            continue
        return row
    return None


def sit_forcing_friction(
    digest: dict[str, Any], leftover: dict[str, Any]
) -> dict[str, Any] | None:
    """Sit leftover with a charter forcing friction — row-bind path, not grok."""
    if leftover.get("leftover") != LEFTOVER_SIT:
        return None
    return undispositioned_forcing_friction(digest)


def parse_row_class(text: object) -> dict[str, str] | None:
    """Parse one ROW_CLASS block.

    Valid ``low|trio`` ⇒ class dict. Ambiguous ``ROW_CLASS:`` line ⇒
    ``class=ROW_CLASS_HOLD`` (ticker parks, does not re-bind). Missing ⇒ ``None``.
    """
    body = str(text or "")
    match = _ROW_CLASS_RE.search(body)
    if match:
        parsed: dict[str, str] = {"class": match.group(1).lower()}
    elif _AMBIGUOUS_CLASS_RE.search(body):
        parsed = {"class": ROW_CLASS_HOLD}
    else:
        return None
    why = _ROW_WHY_RE.search(body)
    if why:
        parsed["why"] = why.group(1).strip()
    row_id = _ROW_ID_RE.search(body)
    if row_id:
        parsed["row_id"] = row_id.group(1).strip()
    return parsed


def _root_turns(digest: dict[str, Any]) -> list[dict[str, Any]]:
    """Unread + recent, unique by ``turn_number``, ascending.

    Caller reverses for newest-first. Unread wins when the same number appears
    in both lists. Turns without a number trail in encounter order.
    """
    root = digest.get("root") or {}
    by_num: dict[int, dict[str, Any]] = {}
    unnumbered: list[dict[str, Any]] = []
    for key in ("unread_turns", "recent_turns"):
        for turn in root.get(key) or []:
            if not isinstance(turn, dict):
                continue
            num = turn.get("turn_number")
            if isinstance(num, int):
                by_num.setdefault(num, turn)
            else:
                unnumbered.append(turn)
    return [by_num[n] for n in sorted(by_num)] + unnumbered


def absorb_row_classes_from_digest(
    digest: dict[str, Any], state: dict[str, Any]
) -> None:
    """Scan root turns newest-first; latch the first ROW_CLASS per explicit ``a:`` id.

    Attribution is ``ROW_ID`` (must be ``a:N``) or an ``a:`` token in the turn
    **subject** only — never body/ROW_WHY, never newest-forcing fallback. A
    turn that cannot be attributed is skipped; bind-spawned + no latch is HOLD.
    """
    store = dict(state.get("row_class") or {})
    for turn in reversed(_root_turns(digest)):
        parsed = parse_row_class(turn.get("body") or "") or parse_row_class(
            turn.get("subject") or ""
        )
        if not parsed:
            continue
        fid = _explicit_friction_id(parsed, turn)
        if not fid:
            continue
        if fid in store and store[fid].get("class"):
            continue
        store[fid] = {
            "class": parsed["class"],
            "why": parsed.get("why", ""),
            "row_id": parsed.get("row_id", fid),
            "at": str(turn.get("created_at") or _utcnow()),
            "turn_number": turn.get("turn_number"),
        }
    if store:
        state["row_class"] = dict(list(store.items())[-_KEEP:])


def _explicit_friction_id(parsed: dict[str, str], turn: dict[str, Any]) -> str | None:
    """ROW_ID when it is ``a:N``, else the turn subject. Never body / digest fallback."""
    row_id = str(parsed.get("row_id") or "").strip()
    if row_id.startswith("a:"):
        return row_id.split()[0]
    subject = str(turn.get("subject") or "")
    match = re.search(r"\ba:(\d+)\b", subject)
    if match:
        return f"a:{match.group(1)}"
    return None


def latched_row_class(state: dict[str, Any], friction_id: str) -> dict[str, Any] | None:
    """Return latched class entry for ``friction_id``, or ``None``."""
    entry = (state.get("row_class") or {}).get(friction_id)
    if not entry or not entry.get("class"):
        return None
    cls = str(entry["class"]).lower()
    if cls not in (ROW_CLASS_LOW, ROW_CLASS_TRIO):
        return None
    return entry


def row_bind_spawned(state: dict[str, Any], friction_id: str) -> bool:
    """True once the ticker has fired a row-bind for this friction."""
    return friction_id in (state.get("friction_rows_seen") or {})


def row_class_hold(state: dict[str, Any], friction_id: str) -> bool:
    """Terminal hold: bind completed but ROW_CLASS missing or ambiguous."""
    if not row_bind_spawned(state, friction_id):
        return False
    return latched_row_class(state, friction_id) is None


def todo_slug_for_trio(latched: dict[str, Any], friction: dict[str, Any]) -> str | None:
    """Resolve todo slug for TRIO play path from ROW_ID or friction context."""
    for blob in (latched.get("row_id"), latched.get("why")):
        slug = extract_todo_slug(blob)
        if slug:
            return slug
    return extract_todo_slug(friction.get("note"))


def promote_friction_attention(
    digest: dict[str, Any], friction: dict[str, Any]
) -> list[dict[str, Any]]:
    """Wake item for bind→STOP→fire. ``actionable_attention`` treats kind=friction
    with no ``turns`` as live (fail-closed wake). ``digest`` is unused; kept so
    call sites can pass the harvest snapshot they already hold."""
    del digest
    return [
        {
            "kind": "friction",
            "id": friction["id"],
            "owner": friction.get("owner"),
            "category": friction.get("category"),
            "note": friction.get("note"),
        }
    ]


def row_class_fired(state: dict[str, Any], friction_id: str) -> bool:
    """True once LOW/TRIO has been ticker-fired for this friction."""
    return friction_id in (state.get("row_class_fired") or {})


def mark_row_class_fired(
    state: dict[str, Any], friction_id: str, *, at: str | None = None
) -> None:
    """Latch path-fired so a still-open row cannot remint LOW/TRIO."""
    if not friction_id:
        return
    store = dict(state.get("row_class_fired") or {})
    store[friction_id] = at or _utcnow()
    state["row_class_fired"] = dict(list(store.items())[-_KEEP:])


def ready_to_fire_row_class(state: dict[str, Any], friction_id: str) -> bool:
    """Bind spawned, class latched, path not yet fired."""
    return (
        row_bind_spawned(state, friction_id)
        and latched_row_class(state, friction_id) is not None
        and not row_class_fired(state, friction_id)
    )


def ready_row_class_attention(
    digest: dict[str, Any], state: dict[str, Any]
) -> list[dict[str, Any]]:
    """Promote latched-but-unfired rows into digest attention (B2 wake edge)."""
    absorb_row_classes_from_digest(digest, state)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in digest.get("frictions") or []:
        if not isinstance(row, dict):
            continue
        fid = str(row.get("id") or "")
        if not fid or fid in seen or not ready_to_fire_row_class(state, fid):
            continue
        out.extend(promote_friction_attention(digest, row))
        seen.add(fid)
    return out


__all__ = [
    "ROW_CLASS_FIRED",
    "ROW_CLASS_HOLD",
    "ROW_CLASS_LOW",
    "ROW_CLASS_TRIO",
    "absorb_row_classes_from_digest",
    "latched_row_class",
    "mark_row_class_fired",
    "parse_row_class",
    "promote_friction_attention",
    "ready_row_class_attention",
    "ready_to_fire_row_class",
    "row_bind_spawned",
    "row_class_fired",
    "row_class_hold",
    "sit_forcing_friction",
    "todo_slug_for_trio",
    "undispositioned_forcing_friction",
]

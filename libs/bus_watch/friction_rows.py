"""Friction score rows — open frictions on charter-owned services become NOW rows.

Why
---
A house liaison watched its bus lanes but was blind to ``friction()``
observations filed against the services its charter owns (operator, 10595,
2026-09-13: liaisons should "proactively address frictions similar to the
vision of a mutable score by a conductor"). The conductor's score is mutable:
rows enter, need a disposition, and leave. This module applies that posture to
friction assertions **without a second driver** — the tick/wake loop stays the
only driver; frictions are one more row source feeding ``digest["attention"]``
and the induction's NOW line.

Row lifecycle (one row = one friction assertion, id ``a:<assertion_id>``)
-------------------------------------------------------------------------
1. **Harvest** — ``harvest_frictions`` reads open frictions for every owner in
   ``policy.owned_services`` over the Cortex UDS (same read path as the other
   digest HTTP reads). Scope is *declared* in policy, never inferred from bus
   traffic: an empty ``owned_services`` harvests nothing.
2. **Latch** — ``friction_rows_seen[row_id] = tick_iso`` (loop-owned tick-state
   key) is written when the ticker spawns on a row, so a friction enters the
   spawn channel once per assertion id. A re-opened friction carries a new id
   and enters again. The row stays a NOW candidate until dispositioned.
3. **Disposition** — the seat binds ``direct-first | todo-minted | declined``
   with ``liaison-tick.py --mark-friction a:<id>:<disposition>`` (operator key
   ``friction_dispositions``) and writes back on the assertion with
   ``cortex(tool="friction_close")`` (``todo:<slug>`` · ``wontfix`` · at landing
   ``commit:<sha>``). A closed friction is superseded and leaves the harvest.
4. **Guards** — charter scope; ``policy.friction_dispatch_cap`` ticker-minted
   friction spawns per night (``current_night_id`` = UTC day; default 3); the
   conductor's REPEATED_FAILURE rule — a second ``direct-first`` mark on the
   same row flags ``repeated_failure``: consult, then ``todo-minted | declined``,
   never a third variant.

Non-goals: no spawn-predicate change, no pipeline change, no ticker change.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

import httpx
from transport_utils import DEFAULT_CORTEX_URL, make_sync_client

DISPOSITIONS = ("direct-first", "todo-minted", "declined")
DEFAULT_FRICTION_DISPATCH_CAP = 3
REPEATED_FAILURE_ATTEMPTS = 2
# Mirrors cortex_store.dispatch_ops._shared._FRICTION_CATEGORIES minus ``feature``:
# a feature friction is an ask (cortex ``actionable=false``) and a liaison has no
# authority to commission work, so it never becomes a score row.
HARVEST_CATEGORIES = frozenset(
    {
        "tool_mismatch",
        "tool_absent",
        "tool_error",
        "schema_gap",
        "boot_drift",
        "lesson_gap",
        "lesson_conflict",
        "stale_context",
        "doc_drift",
        "protocol",
        "regression",
    }
)
_OWNER_PREFIXES = ("service:", "agent_skill:", "ai_agent:")
_CATEGORY_RE = re.compile(r"^\[(?P<category>[a-z_]+)\]\s*(?P<note>.*)", re.DOTALL)
# Newest-first window per owner; ``[`` narrows the LIKE to bracketed claims so the
# window is spent on frictions, not operational notes (service:agent-bus carried
# 100+ bracketed rows on 2026-09-13; older open frictions wait for the newer ones
# to leave).
_FETCH_LIMIT = 100
_DIGEST_ROWS = 12
_NOTE_CHARS = 160
_EVENT_NOTE_CHARS = 56
_KEEP = 200
_FORCING_STATES = frozenset({"open", "repeated_failure"})


def owned_services(policy: dict[str, Any]) -> list[str]:
    """Charter-owned friction owners as canonical entity ids, in policy order.

    Accepts a JSON list or a comma string (``--set owned_services=agent-bus,cortex``);
    bare slugs become ``service:<slug>``; blanks and repeats drop.
    """
    raw = policy.get("owned_services") or []
    parts = raw.split(",") if isinstance(raw, str) else list(raw)
    out: list[str] = []
    for part in parts:
        slug = str(part).strip()
        if not slug:
            continue
        owner = slug if slug.startswith(_OWNER_PREFIXES) else f"service:{slug}"
        if owner not in out:
            out.append(owner)
    return out


def friction_dispatch_cap(policy: dict[str, Any]) -> int:
    """Ticker-minted friction spawns allowed per night (``>= 0``; default 3)."""
    try:
        return max(
            0, int(policy.get("friction_dispatch_cap", DEFAULT_FRICTION_DISPATCH_CAP))
        )
    except (TypeError, ValueError):
        return DEFAULT_FRICTION_DISPATCH_CAP


def row_id(assertion: Any) -> str:
    """``33355`` or ``a:33355`` → ``a:33355``; anything else is a ``ValueError``."""
    digits = str(assertion).strip().removeprefix("a:")
    if not digits.isdigit():
        raise ValueError(
            f"friction row wants a numeric assertion id, got {assertion!r}"
        )
    return f"a:{digits}"


def _cortex_rows(owner: str) -> list[dict[str, Any]]:
    """Newest-first non-superseded bracketed assertions on ``owner`` (summary rows)."""
    body = {
        "tool": "assertions",
        "arguments": {
            "entity_id": owner,
            "filter": "[",
            "superseded": False,
            "limit": _FETCH_LIMIT,
            "intent": "summary",
        },
    }
    with make_sync_client(DEFAULT_CORTEX_URL, timeout=10.0) as client:
        response = client.post("/dispatch", json=body)
    if response.status_code >= 400:
        raise httpx.HTTPStatusError(
            f"http_{response.status_code}", request=response.request, response=response
        )
    payload = response.json()
    if isinstance(payload, dict) and payload.get("error"):
        raise ValueError(str(payload["error"])[:200])
    return list((payload or {}).get("items") or []) if isinstance(payload, dict) else []


def _parse_row(raw: dict[str, Any], owner: str) -> dict[str, Any] | None:
    """One summary assertion → a bare score row, or ``None`` when it is not an
    open friction on ``owner`` (closure rows, notes, feature asks, foreign owner,
    or non-actionable / defer-enqueue rows per summary projection)."""
    if str(raw.get("entity_id") or owner) != owner or raw.get("superseded_by"):
        return None
    if raw.get("defer_enqueue") or raw.get("actionable") is False:
        return None
    match = _CATEGORY_RE.match(str(raw.get("claim") or ""))
    if not match or match.group("category") not in HARVEST_CATEGORIES:
        return None
    try:
        rid = row_id(raw.get("id"))
    except ValueError:
        return None
    return {
        "id": rid,
        "owner": owner,
        "category": match.group("category"),
        "note": " ".join(match.group("note").split())[:_NOTE_CHARS],
        "observed_at": raw.get("observed_at"),
        "confidence": raw.get("confidence"),
    }


def _row_state(mark: dict[str, Any] | None) -> str:
    """``open`` (needs a disposition) · ``in_flight`` (direct-first, first or second
    attempt) · ``repeated_failure`` (a direct-first re-mark: consult, no third
    variant) · ``close_pending`` (todo-minted/declined but not yet closed on the
    assertion)."""
    if not mark:
        return "open"
    if mark.get("disposition") != "direct-first":
        return "close_pending"
    if int(mark.get("attempts") or 0) >= REPEATED_FAILURE_ATTEMPTS:
        return "repeated_failure"
    return "in_flight"


def build_rows(
    raw_by_owner: dict[str, list[dict[str, Any]]], state: dict[str, Any]
) -> list[dict[str, Any]]:
    """Fold harvested assertions with the tick-state latches into score rows,
    newest observation first."""
    seen = state.get("friction_rows_seen") or {}
    marks = state.get("friction_dispositions") or {}
    rows: list[dict[str, Any]] = []
    for owner, raws in raw_by_owner.items():
        for raw in raws:
            row = _parse_row(raw, owner)
            if row is None:
                continue
            mark = marks.get(row["id"])
            row_state = _row_state(mark)
            row.update(
                {
                    "seen_at": seen.get(row["id"]),
                    "disposition": mark,
                    "state": row_state,
                    "forcing": row_state in _FORCING_STATES,
                }
            )
            rows.append(row)
    rows.sort(key=lambda r: str(r.get("observed_at") or ""), reverse=True)
    return rows


def dispatched_tonight(state: dict[str, Any], night_id: str) -> int:
    """Friction spawns latched tonight — the latch timestamp *is* the counter."""
    seen = state.get("friction_rows_seen") or {}
    return sum(1 for ts in seen.values() if str(ts).startswith(night_id))


def _first_forcing_row(
    rows: list[dict[str, Any]], *, require_unseen: bool
) -> dict[str, Any] | None:
    """Newest-first selector shared by ``promote`` and ``now_row`` (G26/AC7).

    ``require_unseen=True`` skips rows already latched in ``friction_rows_seen``
    so a promoted row does not remain NOW for a successor tick.
    """
    for row in rows:
        if not row.get("forcing"):
            continue
        if require_unseen and row.get("seen_at"):
            continue
        return row
    return None


def promote(rows: list[dict[str, Any]], *, cap_remaining: int) -> list[dict[str, Any]]:
    """The attention item for the newest forcing row the ticker has not spawned
    on — one per tick, none once the night's cap is spent.

    One spawn is one seat for one friction: latching several rows on a single
    spawn would spend their once-only entry on a successor whose NOW names just
    the first of them.
    """
    if cap_remaining <= 0:
        return []
    row = _first_forcing_row(rows, require_unseen=True)
    if row is None:
        return []
    return [
        {
            "kind": "friction",
            "id": row["id"],
            "owner": row["owner"],
            "category": row["category"],
            "note": row["note"][:_EVENT_NOTE_CHARS],
        }
    ]


def harvest_frictions(
    state: dict[str, Any], policy: dict[str, Any], *, night_id: str
) -> dict[str, Any]:
    """Read every owned service and return ``{rows, attention, summary}``.

    A Cortex read failure is reported in ``summary.error`` and yields no rows for
    that owner — the digest must still build; the seat sees the gap, not a crash.
    """
    owners = owned_services(policy)
    raw_by_owner: dict[str, list[dict[str, Any]]] = {}
    errors: list[str] = []
    for owner in owners:
        try:
            raw_by_owner[owner] = _cortex_rows(owner)
        except (httpx.HTTPError, ValueError) as exc:
            errors.append(f"{owner}: {type(exc).__name__}: {exc}"[:160])
    rows = build_rows(raw_by_owner, state)
    cap = friction_dispatch_cap(policy)
    used = dispatched_tonight(state, night_id)
    attention = promote(rows, cap_remaining=max(0, cap - used))
    summary: dict[str, Any] = {
        "owners": owners,
        "open": len(rows),
        "forcing": sum(1 for r in rows if r["forcing"]),
        "promoted": len(attention),
        "dispatch_cap": cap,
        "dispatched_tonight": used,
        "night_id": night_id,
        "window_per_owner": _FETCH_LIMIT,
    }
    if errors:
        summary["error"] = "; ".join(errors)
    return {"rows": rows[:_DIGEST_ROWS], "attention": attention, "summary": summary}


def fold_fingerprint(fingerprint: str, rows: list[dict[str, Any]]) -> str:
    """A new forcing row is a change the attended loop must wake on; houses with
    no forcing rows keep the bus-only fingerprint unchanged."""
    forcing = [r["id"] for r in rows if r.get("forcing")]
    if not forcing:
        return fingerprint
    seed = f"{fingerprint}|{','.join(sorted(forcing))}".encode()
    return hashlib.sha1(seed).hexdigest()[: max(len(fingerprint), 12)]


def latch_rows(state: dict[str, Any], attention: Any, *, at: str) -> None:
    """After a successful spawn: every friction item the successor was spawned
    for enters ``friction_rows_seen`` — once per assertion id."""
    seen = dict(state.get("friction_rows_seen") or {})
    for item in attention if isinstance(attention, list) else []:
        if isinstance(item, dict) and item.get("kind") == "friction" and item.get("id"):
            seen[str(item["id"])] = at
    if seen:
        state["friction_rows_seen"] = dict(list(seen.items())[-_KEEP:])


def parse_mark(spec: str) -> tuple[str, str]:
    """``a:33355:direct-first`` (or ``33355:declined``) → ``("a:33355", disposition)``."""
    head, sep, disposition = spec.strip().rpartition(":")
    if not sep or disposition not in DISPOSITIONS:
        raise ValueError(
            f"--mark-friction wants <assertion>:<{'|'.join(DISPOSITIONS)}>, got {spec!r}"
        )
    return row_id(head), disposition


def mark_friction(
    state: dict[str, Any], rid: str, disposition: str, *, at: str
) -> dict[str, Any]:
    """Bind a disposition on a row (operator key ``friction_dispositions``).

    Each ``direct-first`` mark counts one attempt; the second flips the row to
    ``repeated_failure``. ``todo-minted`` / ``declined`` reset attempts — the
    row now waits for its ``friction_close``.
    """
    marks = dict(state.get("friction_dispositions") or {})
    prior = marks.get(rid) or {}
    attempts = (
        int(prior.get("attempts") or 0) + 1 if disposition == "direct-first" else 0
    )
    marks[rid] = {"disposition": disposition, "at": at, "attempts": attempts}
    state["friction_dispositions"] = dict(list(marks.items())[-_KEEP:])
    return marks[rid]


def event_line(item: dict[str, Any]) -> str:
    """``Event:`` text for a promoted friction attention item."""
    return (
        f"friction {item.get('id')} [{item.get('category')}] {item.get('owner')} "
        f"«{str(item.get('note') or '')[:_EVENT_NOTE_CHARS]}»"
    )


def now_row(digest: dict[str, Any]) -> str:
    """The newest unlatched forcing friction as a NOW dispatch row, or ``""``.

    Renders the disposition verb and the one-shot that records it, so the seat
    dispatches the row instead of noting it (the 10479 "Next: R12 recon" STAY).
    Uses the same ``require_unseen`` selector as ``promote`` (G26/AC7).
    """
    row = _first_forcing_row(list(digest.get("frictions") or []), require_unseen=True)
    if row is None:
        return ""
    head = f"Friction {row['id']} [{row['category']}] {row['owner']}"
    if row.get("state") == "repeated_failure":
        attempts = int((row.get("disposition") or {}).get("attempts") or 0)
        return (
            f"{head} — REPEATED_FAILURE (direct-first ×{attempts}): consult, then "
            f"--mark-friction {row['id']}:todo-minted|declined + friction_close; "
            "never a third variant"
        )
    return (
        f"{head} «{row['note'][:_EVENT_NOTE_CHARS]}» → disposition direct-first | "
        f"todo-minted | declined (--mark-friction {row['id']}:<d>; close-back "
        "friction_close on the assertion)"
    )


__all__ = [
    "DISPOSITIONS",
    "HARVEST_CATEGORIES",
    "build_rows",
    "dispatched_tonight",
    "event_line",
    "fold_fingerprint",
    "friction_dispatch_cap",
    "harvest_frictions",
    "latch_rows",
    "mark_friction",
    "now_row",
    "owned_services",
    "parse_mark",
    "promote",
    "row_id",
]

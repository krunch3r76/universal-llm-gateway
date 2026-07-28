"""Standing charter-friction conveyor — enroll follow-on todos on tick."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cortex_store.dispatch_ops._friction_enqueue import (
    normalize_charter_root,
    todo_exists_for_friction,
)
from universal_logging import get_logger

from scripts.model_manager import observation_event_conveyor as conv_events

from . import bus_client
from .checkpoint_parse import parse_checkpoint
from .checkpoint_schema import emit_footer
from .eligibility import ENROLLMENT_TAG
from .friction_ledger import CONVEYOR_OFF_TAG
from .pickup_advance import gid_of_row
from .window_sequence import next_window_index, window_id_for
from .window_terminal_contract import is_tip_class

logger = get_logger(__name__)

CONVEYOR_SLUG = "charter-friction-conveyor"
CONVEYOR_STALE_TICKS = 48
_STATE_DIR = Path.home() / ".local/share" / "charter-runner"
_STATE_PATH = _STATE_DIR / "conveyor-enrollments.json"


@dataclass
class EnrollmentRecord:
    friction_id: int
    todo_slug: str
    root_id: str
    ticks_idle: int = 0
    stale: bool = False


def _load_state() -> dict[str, Any]:
    if not _STATE_PATH.is_file():
        return {"enrollments": {}, "on_conveyor": {}}
    try:
        data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"enrollments": {}, "on_conveyor": {}}
    if not isinstance(data, dict):
        return {"enrollments": {}, "on_conveyor": {}}
    data.setdefault("enrollments", {})
    data.setdefault("on_conveyor", {})
    return data


def _save_state(data: dict[str, Any]) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _record_from_raw(friction_id: str, raw: dict[str, Any]) -> EnrollmentRecord:
    return EnrollmentRecord(
        friction_id=int(friction_id),
        todo_slug=str(raw.get("todo_slug") or ""),
        root_id=str(raw.get("root_id") or ""),
        ticks_idle=int(raw.get("ticks_idle") or 0),
        stale=bool(raw.get("stale")),
    )


def is_on_conveyor(friction_id: int, todo_slug: str | None = None) -> bool:
    """True when friction/todo is actively enrolled on the conveyor."""
    state = _load_state()
    on = state.get("on_conveyor") or {}
    key = str(friction_id)
    if key not in on:
        return False
    rec = _record_from_raw(key, on[key])
    return not rec.stale and (not todo_slug or rec.todo_slug == todo_slug)


def is_stale_unenrolled(friction_id: int) -> bool:
    state = _load_state()
    raw = (state.get("enrollments") or {}).get(str(friction_id))
    if not isinstance(raw, dict):
        return False
    return bool(raw.get("stale"))


def _conveyor_pickup_rows(turns: list[dict[str, Any]]) -> list[str]:
    latest_body = ""
    for turn in sorted(turns, key=lambda t: int(t.get("turn_number") or 0), reverse=True):
        if is_tip_class(str(turn.get("subject") or ""), body=str(turn.get("body") or "")):
            latest_body = str(turn.get("body") or "")
            break
    if not latest_body:
        return []
    from .checkpoint_body import resolve_checkpoint_body

    parsed = parse_checkpoint(resolve_checkpoint_body(latest_body))
    return list(parsed.next_pickup)


def _row_for_friction(
    *,
    friction_id: int,
    todo_slug: str,
    root_id: str,
    note: str,
    detent: str | None = None,
) -> str:
    short = note[:80].strip() or f"friction #{friction_id}"
    root = normalize_charter_root(root_id)
    detent_tok = f" · detent={detent}" if detent else ""
    return (
        f"G1 — `{todo_slug}` follow-on from root {root} "
        f"(spawned_by_friction={friction_id}){detent_tok} · {short}"
    )


def _already_on_conveyor(rows: list[str], *, friction_id: int, todo_slug: str) -> bool:
    token = f"spawned_by_friction={friction_id}"
    for row in rows:
        if token in row or todo_slug in row:
            return True
    return False


async def ensure_conveyor_root() -> str:
    """Return the standing conveyor root id, creating it on first use.

    Also idempotently ledger-seeds the resolved id so Phase-3 kernel does not
    ``kernel_unseeded``-starve the conveyor after manage recycle (a:26619).
    """
    from .seed_phase1 import conveyor_default_seed, ensure_root_ledger_seed

    existing = await bus_client.find_thread_id_by_slug(CONVEYOR_SLUG)
    if existing:
        root_id = existing
    else:
        root_id = await bus_client.create_thread(
            slug=CONVEYOR_SLUG,
            summary="Standing fleet conveyor for charter friction follow-ons",
            tags=[ENROLLMENT_TAG],
            enroll_charter_runner=True,
        )
    ensure_root_ledger_seed(root_id, default=conveyor_default_seed(root_id))
    return root_id


def _conveyor_footer(conveyor_id: str, turns: list[dict[str, Any]], gid: str) -> str:
    """``charter-state`` block for a conveyor-authored CHECKPOINT.

    The conveyor posts its own tips, so it must satisfy the same harvest footer gate
    as a worker seat — a footerless conveyor tip fails closed and strands every
    follow-on row it carries (a:26625). ``window_id`` names the window this state
    follows, since a pickup append is a state post between windows, not a closeout.
    """
    high = max(next_window_index(conveyor_id, turns) - 1, 0)
    return emit_footer(
        status="CHECKPOINT",
        next_pickup={"gid": gid, "lane": "judgment", "executor": "pending"},
        wip=None,
        consult={"role": None, "poll_hint": None, "from": None},
        revise_count=0,
        evidence=[],
        window_id=window_id_for(conveyor_id, high),
        transition_id=None,
    )


async def _append_conveyor_pickup(*, conveyor_id: str, row: str) -> None:
    turns = await bus_client.fetch_turns(conveyor_id)
    existing = _conveyor_pickup_rows(turns)
    if row in existing:
        return
    merged = existing + [row]
    pickup_block = "\n".join(f"- {item}" for item in merged)
    subject = "CHECKPOINT — charter-friction-conveyor pickup append"
    body = f"""# {subject}

## WIP / In-flight
_None this window._

## Next-pickup
{pickup_block}

## Steps
1. [ ] Process friction follow-on todos as they arrive

## Frictions
_None this window._

## Sidecars
_None this window._

## BLOCKED
None.

— RESUME (any seat, no command): friction conveyor — process gated Next-pickup.

{_conveyor_footer(conveyor_id, turns, gid_of_row(merged[0]) or "pending")}"""
    await bus_client.post_root_checkpoint(conveyor_id, subject=subject, body=body)


async def enroll_rows(
    *,
    root_id: str,
    root_tags: list[str] | None,
    friction_rows: list[dict[str, Any]],
) -> list[str]:
    """Enroll minted follow-ons on the conveyor; return newly enrolled todo slugs."""
    tags = list(root_tags or [])
    if ENROLLMENT_TAG not in tags or CONVEYOR_OFF_TAG in tags:
        return []

    eligible: list[dict[str, Any]] = []
    for row in friction_rows:
        if not isinstance(row, dict) or row.get("id") is None:
            continue
        attrs = row.get("attributes") or {}
        if not isinstance(attrs, dict):
            attrs = {}
        if attrs.get("defer_enqueue"):
            continue
        if todo_exists_for_friction(int(row["id"])):
            eligible.append(row)
    if not eligible:
        return []

    enrolled: list[str] = []
    conveyor_id = await ensure_conveyor_root()
    turns = await bus_client.fetch_turns(conveyor_id)
    pickup_rows = _conveyor_pickup_rows(turns)
    state = _load_state()
    on_conveyor = dict(state.get("on_conveyor") or {})
    enrollments = dict(state.get("enrollments") or {})

    for row in eligible:
        friction_id = int(row["id"])
        attrs = row.get("attributes") or {}
        if not isinstance(attrs, dict):
            attrs = {}
        todo_slug = todo_exists_for_friction(friction_id)
        if not todo_slug:
            continue
        if _already_on_conveyor(pickup_rows, friction_id=friction_id, todo_slug=todo_slug):
            on_conveyor[str(friction_id)] = {
                "todo_slug": todo_slug,
                "root_id": root_id,
                "ticks_idle": 0,
                "stale": False,
            }
            continue
        note = str(attrs.get("note") or row.get("claim") or "")
        detent = attrs.get("detent")
        if detent is None and todo_slug:
            # Prefer durable todo attr when friction row omitted detent.
            try:
                from cortex_store.dispatch_ops.ops_entities import _op_entity_get

                ent = _op_entity_get(entity_id=todo_slug, intent="full")
                if "error" not in ent:
                    detent = (ent.get("attributes") or {}).get("detent")
            except Exception:  # noqa: BLE001 — row still enrolls without detent
                detent = None
        pickup = _row_for_friction(
            friction_id=friction_id,
            todo_slug=todo_slug,
            root_id=root_id,
            note=note,
            detent=str(detent) if detent else None,
        )
        await _append_conveyor_pickup(conveyor_id=conveyor_id, row=pickup)
        pickup_rows.append(pickup)
        record = {
            "todo_slug": todo_slug,
            "root_id": root_id,
            "ticks_idle": 0,
            "stale": False,
        }
        on_conveyor[str(friction_id)] = record
        enrollments[str(friction_id)] = record
        enrolled.append(todo_slug)
        await conv_events.emit_manage_charter_conveyor_enrolled(
            root=root_id,
            friction_id=friction_id,
            todo_slug=todo_slug,
            conveyor_root=conveyor_id,
        )

    state["on_conveyor"] = on_conveyor
    state["enrollments"] = enrollments
    _save_state(state)
    return enrolled


async def sweep_stale_enrollments() -> list[int]:
    """Demote enrollments idle for CONVEYOR_STALE_TICKS; return stale friction ids."""
    state = _load_state()
    on_conveyor = dict(state.get("on_conveyor") or {})
    enrollments = dict(state.get("enrollments") or {})
    stale_ids: list[int] = []
    for key, raw in list(on_conveyor.items()):
        if not isinstance(raw, dict) or raw.get("stale"):
            continue
        ticks = int(raw.get("ticks_idle") or 0) + 1
        raw["ticks_idle"] = ticks
        if ticks >= CONVEYOR_STALE_TICKS:
            raw["stale"] = True
            friction_id = int(key)
            stale_ids.append(friction_id)
            enrollments[key] = dict(raw)
            await conv_events.emit_manage_charter_conveyor_stale(
                friction_id=friction_id,
                todo_slug=str(raw.get("todo_slug") or ""),
                root=str(raw.get("root_id") or ""),
                ticks_idle=ticks,
            )
        on_conveyor[key] = raw
    state["on_conveyor"] = on_conveyor
    state["enrollments"] = enrollments
    _save_state(state)
    return stale_ids


__all__ = [
    "CONVEYOR_SLUG",
    "CONVEYOR_STALE_TICKS",
    "enroll_rows",
    "ensure_conveyor_root",
    "is_on_conveyor",
    "is_stale_unenrolled",
    "sweep_stale_enrollments",
]

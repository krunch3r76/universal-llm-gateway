"""Standing charter-friction conveyor — enroll follow-on todos on tick."""

from __future__ import annotations

import json
import re
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
from .admission import ENROLLMENT_TAG
from .checkpoint_schema import (
    FOOTER_FENCE,
    emit_footer,
    parse_checkpoint,
    split_sections,
)
from .friction_ledger import CONVEYOR_OFF_TAG
from .storm_fuse import is_quarantined
from .pickup_advance import gid_of_row
from .window_sequence import next_window_index, window_id_for
from .window_terminal_contract import is_tip_class, is_window_terminal

logger = get_logger(__name__)

# Standing conveyor root is the F3 birth charter (6186), not the retired
# `charter-friction-conveyor` slug (6171 closed → empty 6191 shell re-mint).
CONVEYOR_SLUG = "charter-friction-enroll-on-arrival"
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
    from .checkpoint_schema import resolve_checkpoint_body

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

    Preserve the prior window-terminal tip's executor — hardcoding ``pending`` made
    pickup appends the admit tip and clobbered densify G4 gpt → grok (6110 storm).
    """
    high = max(next_window_index(conveyor_id, turns) - 1, 0)
    executor = "pending"
    lane = "judgment"
    for turn in sorted(turns, key=lambda t: int(t.get("turn_number") or 0), reverse=True):
        subj = str(turn.get("subject") or "")
        body = str(turn.get("body") or "")
        if not is_window_terminal(subj, body=body):
            continue
        from .checkpoint_schema import resolve_checkpoint_body
        from .pickup_advance import gated_pickup_from_parsed

        prior = parse_checkpoint(resolve_checkpoint_body(body))
        live = gated_pickup_from_parsed(prior)
        if live is not None:
            gid = live.gid or gid
            if live.lane:
                lane = live.lane
            if live.executor:
                executor = live.executor
        break
    return emit_footer(
        status="CHECKPOINT",
        next_pickup={"gid": gid, "lane": lane, "executor": executor},
        wip=None,
        consult={"role": None, "poll_hint": None, "from": None},
        revise_count=0,
        evidence=[],
        window_id=window_id_for(conveyor_id, high),
        transition_id=None,
    )


_NEXT_PICKUP_SECTION_RE = re.compile(
    r"(?m)^##\s+(Next[- ]pickup)\s*\n.*?(?=^##\s+|^—\s+RESUME|^```\s*charter-state|\Z)",
    re.DOTALL | re.IGNORECASE,
)


def _latest_tip_turn(turns: list[dict[str, Any]]) -> dict[str, Any] | None:
    for turn in sorted(turns, key=lambda t: int(t.get("turn_number") or 0), reverse=True):
        if is_tip_class(str(turn.get("subject") or ""), body=str(turn.get("body") or "")):
            return turn
    return None


def _is_conveyor_shaped(subject: str, body: str) -> bool:
    if "charter-friction-conveyor pickup append" in subject.lower():
        return True
    return "friction conveyor — process gated next-pickup" in body.lower()


def _is_charter_profile_checkpoint(body: str, subject: str) -> bool:
    """True when the tip is a charter CHECKPOINT worth merging, not conveyor-only."""
    if _is_conveyor_shaped(subject, body):
        return False
    from .checkpoint_schema.footer import validate_checkpoint_footer

    if validate_checkpoint_footer(body).ok:
        return True
    sections = split_sections(body)
    charter_keys = ("steps", "frictions", "sidecars", "in one line", "state", "scoreboard uri")
    if any(key in sections for key in charter_keys):
        return True
    return "— RESUME (any seat, no command):".lower() in body.lower()


def _format_pickup_block(rows: list[str], template_section: str) -> str:
    if re.search(r"(?m)^\s*\d+\.\s", template_section):
        return "\n".join(f"{idx + 1}. {row}" for idx, row in enumerate(rows))
    return "\n".join(f"- {row}" for row in rows)


def _replace_next_pickup_section(body: str, rows: list[str]) -> str:
    match = _NEXT_PICKUP_SECTION_RE.search(body)
    pickup_block = _format_pickup_block(rows, match.group(0) if match else "")
    if match:
        heading = match.group(1)
        replacement = f"## {heading}\n{pickup_block}\n\n"
        return body[: match.start()] + replacement + body[match.end() :]
    insert = f"\n## Next-pickup\n{pickup_block}\n\n"
    for marker in ("— RESUME", f"```{FOOTER_FENCE}"):
        idx = body.find(marker)
        if idx >= 0:
            return body[:idx] + insert + body[idx:]
    return body.rstrip() + insert


def _merge_charter_pickup_body(*, body: str, merged_rows: list[str]) -> str:
    from .checkpoint_schema.footer import (
        _FENCE_TAIL_RE,
        _extract_footer_json,
        append_footer_to_packet,
    )

    footer_data, _ = _extract_footer_json(body)
    trimmed = _FENCE_TAIL_RE.sub("", body.rstrip())
    updated = _replace_next_pickup_section(trimmed, merged_rows)
    if not footer_data:
        return updated
    gid = gid_of_row(merged_rows[0]) if merged_rows else None
    if gid and isinstance(footer_data.get("next_pickup"), dict):
        footer_data = dict(footer_data)
        footer_data["next_pickup"] = dict(footer_data["next_pickup"])
        footer_data["next_pickup"]["gid"] = gid
    return append_footer_to_packet(updated, **footer_data)


def _post_conveyor_pickup_body(*, root_id: str, merged: list[str], turns: list[dict[str, Any]]) -> tuple[str, str]:
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

{_conveyor_footer(root_id, turns, gid_of_row(merged[0]) or "pending")}"""
    return subject, body


async def _append_pickup_to_root(
    *,
    root_id: str,
    row: str,
    turns: list[dict[str, Any]] | None = None,
) -> None:
    """Append a friction follow-on row to the caller root's tip Next-pickup."""
    if turns is None:
        turns = await bus_client.fetch_turns(root_id)
    existing = _conveyor_pickup_rows(turns)
    if row in existing:
        return
    merged = existing + [row]
    latest = _latest_tip_turn(turns)
    if latest and _is_charter_profile_checkpoint(
        str(latest.get("body") or ""),
        str(latest.get("subject") or ""),
    ):
        subject = str(latest.get("subject") or "")
        body = _merge_charter_pickup_body(body=str(latest.get("body") or ""), merged_rows=merged)
        await bus_client.post_root_checkpoint(root_id, subject=subject, body=body)
        return
    subject, body = _post_conveyor_pickup_body(root_id=root_id, merged=merged, turns=turns)
    await bus_client.post_root_checkpoint(root_id, subject=subject, body=body)


async def _append_conveyor_pickup(*, conveyor_id: str, row: str) -> None:
    await _append_pickup_to_root(root_id=conveyor_id, row=row)


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
    if not root_id or not str(root_id).strip():
        return []
    append_root = str(root_id).strip()
    turns = await bus_client.fetch_turns(append_root)
    pickup_rows = _conveyor_pickup_rows(turns)
    state = _load_state()
    on_conveyor = dict(state.get("on_conveyor") or {})
    enrollments = dict(state.get("enrollments") or {})

    for row in eligible:
        friction_id = int(row["id"])
        if is_quarantined(friction_id):
            continue
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
        await _append_pickup_to_root(root_id=append_root, row=pickup, turns=turns)
        pickup_rows.append(pickup)
        from .conveyor_phase import set_conveyor_phase
        from .root_ledger import open_default_ledger

        conn = open_default_ledger()
        try:
            set_conveyor_phase(conn, append_root, "active")
        finally:
            conn.close()
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
            conveyor_root=append_root,
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


async def disenroll_frictions(
    friction_ids: list[int],
    *,
    reason: str,
) -> list[dict[str, Any]]:
    """Remove enrollments from conveyor SoT and emit ``disenrolled`` per id.

    Idempotent: missing ids are skipped (no event). Stale rows still disenroll —
    ``stale`` is demotion, not belt exit.
    """
    state = _load_state()
    on_conveyor = dict(state.get("on_conveyor") or {})
    enrollments = dict(state.get("enrollments") or {})
    removed: list[dict[str, Any]] = []
    for friction_id in friction_ids:
        key = str(friction_id)
        raw = on_conveyor.get(key) or enrollments.get(key)
        if not isinstance(raw, dict):
            continue
        todo_slug = str(raw.get("todo_slug") or "")
        root = str(raw.get("root_id") or "")
        was_stale = bool(raw.get("stale"))
        on_conveyor.pop(key, None)
        enrollments.pop(key, None)
        await conv_events.emit_manage_charter_conveyor_disenrolled(
            friction_id=friction_id,
            todo_slug=todo_slug,
            root=root,
            reason=reason,
            was_stale=was_stale,
        )
        removed.append(
            {
                "friction_id": friction_id,
                "todo_slug": todo_slug,
                "root_id": root,
                "was_stale": was_stale,
            }
        )
    state["on_conveyor"] = on_conveyor
    state["enrollments"] = enrollments
    _save_state(state)
    return removed


__all__ = [
    "CONVEYOR_SLUG",
    "CONVEYOR_STALE_TICKS",
    "disenroll_frictions",
    "enroll_rows",
    "ensure_conveyor_root",
    "is_on_conveyor",
    "is_stale_unenrolled",
    "sweep_stale_enrollments",
]

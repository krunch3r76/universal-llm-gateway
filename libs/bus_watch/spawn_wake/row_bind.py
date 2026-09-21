"""CDP row-bind and ticker-fired LOW / TRIO bodies for forcing-friction sit leftovers."""

from __future__ import annotations

from typing import Any

from bus_watch.fable_lock import current_night_id
from bus_watch.loop_tape import loop_tape_thread
from bus_watch.spawn_wake.play_classify import build_play_dispatch_body
from bus_watch.spawn_wake.row_class import ROW_CLASS_LOW, ROW_CLASS_TRIO

_ROW_BIND_PROMPT = (
    "ROW-BIND (mandatory, bind-only): Classify this forcing friction score row "
    "as LOW or TRIO, post exactly one block on the house thread, then STOP — "
    "do not implement, do not fire remaining hops.\n\n"
    "Post:\n"
    "ROW_CLASS: low|trio\n"
    "ROW_WHY: <one line>\n"
    "ROW_ID: {friction_id}\n\n"
    "LOW = direct mechanical fix (ticker will spawn cursor-sdk implement lane B).\n"
    "TRIO = Sketch → Mission Composer → Conductor (ticker fires the path).\n\n"
    "Friction {friction_id} [{category}] {owner}\n"
    "«{note}»"
)

_LOW_IMPLEMENT_PREAMBLE = (
    "ROW_CLASS LOW (mandatory): Land on green when AC met — merge the lane branch "
    "on green; silence is not a hold-merge gate. CDP review+apply is a separate "
    "ticker-fired hop; this dispatch is mechanical implement only.\n\n"
    "Friction {friction_id} [{category}] {owner}\n"
    "«{note}»\n"
    "{why_line}"
)

_TRIO_SKETCH_PROMPT = (
    "ROW_CLASS TRIO — Sketch consult (mandatory): Bind the friction row into a "
    "Sketch → Mission Composer → Conductor path. Post your sketch/consult outcome "
    "on the house thread; do not implement in-repo. Ticker fires Mission Composer "
    "and Conductor after you STOP.\n\n"
    "Friction {friction_id} [{category}] {owner}\n"
    "«{note}»\n"
    "{why_line}"
)


def row_bind_model(policy: dict[str, Any]) -> str:
    """CDP model for row-bind hops — ``policy.row_bind_model`` or ``cdp/opus-5``."""
    raw = str(policy.get("row_bind_model") or "").strip()
    return raw if raw.startswith("cdp/") else "cdp/opus-5"


def build_row_bind_body(
    root_id: str,
    policy: dict[str, Any],
    friction: dict[str, Any],
) -> dict[str, Any]:
    """CDP bind-only: posts ROW_CLASS then stops. No seat, no lane."""
    max_hop = int(policy.get("max_hop_minutes") or 60)
    fid = str(friction.get("id") or "")
    prompt = _ROW_BIND_PROMPT.format(
        friction_id=fid,
        category=friction.get("category") or "",
        owner=friction.get("owner") or "",
        note=str(friction.get("note") or "")[:160],
    )
    tape = loop_tape_thread(root_id, policy)
    return {
        "op": "generate",
        "model": row_bind_model(policy),
        "contract": "none",
        "prompt": prompt,
        "dispatch_thread_id": tape,
        "work_key": f"row-bind:{fid}:night-{current_night_id()}",
        "timeout_seconds": max_hop * 60 + 1800,
        "caller_agent": "liaison-ticker",
        "_row_bind": True,
    }


def build_low_implement_body(
    root_id: str,
    policy: dict[str, Any],
    friction: dict[str, Any],
    *,
    latched: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ticker-fired LOW path: cursor-sdk implement lane B with land-on-green preamble."""
    max_hop = int(policy.get("max_hop_minutes") or 60)
    fid = str(friction.get("id") or "")
    why = str((latched or {}).get("why") or "").strip()
    why_line = f"ROW_WHY: {why}\n" if why else ""
    prompt = _LOW_IMPLEMENT_PREAMBLE.format(
        friction_id=fid,
        category=friction.get("category") or "",
        owner=friction.get("owner") or "",
        note=str(friction.get("note") or "")[:160],
        why_line=why_line,
    )
    tape = loop_tape_thread(root_id, policy)
    return {
        "op": "generate",
        "seat": "cursor-sdk",
        "contract": "implement",
        "lane": "B",
        "source_ref": fid,
        "work_key": fid,
        "prompt": prompt,
        "dispatch_thread_id": tape,
        "timeout_seconds": max_hop * 60 + 1800,
        "caller_agent": "liaison-ticker",
        "_row_class": ROW_CLASS_LOW,
    }


def build_trio_sketch_body(
    root_id: str,
    policy: dict[str, Any],
    friction: dict[str, Any],
    *,
    latched: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ticker-fired TRIO without ``todo:{slug}``: CDP sketch consult, no seat."""
    max_hop = int(policy.get("max_hop_minutes") or 60)
    fid = str(friction.get("id") or "")
    why = str((latched or {}).get("why") or "").strip()
    why_line = f"ROW_WHY: {why}\n" if why else ""
    prompt = _TRIO_SKETCH_PROMPT.format(
        friction_id=fid,
        category=friction.get("category") or "",
        owner=friction.get("owner") or "",
        note=str(friction.get("note") or "")[:160],
        why_line=why_line,
    )
    tape = loop_tape_thread(root_id, policy)
    return {
        "op": "generate",
        "model": "cdp/opus-5",
        "contract": "none",
        "prompt": prompt,
        "dispatch_thread_id": tape,
        "work_key": f"row-trio-sketch:{fid}:night-{current_night_id()}",
        "timeout_seconds": max_hop * 60 + 1800,
        "caller_agent": "liaison-ticker",
        "_row_class": ROW_CLASS_TRIO,
    }


def build_trio_fire_body(
    root_id: str,
    policy: dict[str, Any],
    friction: dict[str, Any],
    *,
    latched: dict[str, Any] | None = None,
    todo_slug: str | None = None,
) -> dict[str, Any]:
    """TRIO: play conductor when ``todo:{slug}`` is named; else CDP sketch consult."""
    if todo_slug:
        body = build_play_dispatch_body(root_id, policy, todo_slug=todo_slug)
        body["_row_class"] = ROW_CLASS_TRIO
        return body
    return build_trio_sketch_body(root_id, policy, friction, latched=latched)


def body_for_sit_friction(
    root_id: str,
    policy: dict[str, Any],
    friction: dict[str, Any],
    state: dict[str, Any],
    digest: dict[str, Any],
) -> dict[str, Any] | None:
    """Resolve sit+forcing-friction spawn body: bind, fire, or hold sentinel."""
    from bus_watch.spawn_wake.row_class import (
        ROW_CLASS_HOLD,
        ROW_CLASS_LOW,
        ROW_CLASS_TRIO,
        absorb_row_classes_from_digest,
        latched_row_class,
        row_class_hold,
        todo_slug_for_trio,
    )

    absorb_row_classes_from_digest(digest, state)
    fid = str(friction.get("id") or "")
    latched = latched_row_class(state, fid)
    if latched:
        cls = latched["class"]
        if cls == ROW_CLASS_LOW:
            return build_low_implement_body(
                root_id, policy, friction, latched=latched
            )
        if cls == ROW_CLASS_TRIO:
            slug = todo_slug_for_trio(latched, friction)
            return build_trio_fire_body(
                root_id,
                policy,
                friction,
                latched=latched,
                todo_slug=slug,
            )
    if row_class_hold(state, fid):
        return {"_row_class_hold": True, "_refused": ROW_CLASS_HOLD}
    return build_row_bind_body(root_id, policy, friction)


__all__ = [
    "body_for_sit_friction",
    "build_low_implement_body",
    "build_row_bind_body",
    "build_trio_fire_body",
    "build_trio_sketch_body",
    "row_bind_model",
]

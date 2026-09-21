"""CDP row-bind and ticker-fired LOW / TRIO bodies for forcing-friction sit leftovers."""

from __future__ import annotations

from typing import Any

from bus_watch.fable_lock import current_night_id
from bus_watch.liaison_pager import page_liaison
from bus_watch.loop_tape import loop_tape_thread
from bus_watch.spawn_wake.play_classify import build_play_dispatch_body
from bus_watch.spawn_wake.row_class import (
    ROW_CLASS_FIRED,
    ROW_CLASS_HOLD,
    ROW_CLASS_LOW,
    ROW_CLASS_TRIO,
    absorb_row_classes_from_digest,
    latched_row_class,
    row_class_fired,
    row_class_hold,
    todo_slug_for_trio,
)

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

_DISPOSITION_EXIT = (
    "Close the score row: --mark-friction {friction_id}:direct-first|todo-minted|"
    "declined then friction_close on the assertion (commit:<sha> at land). "
    "Do not leave the row forcing."
)

_LOW_IMPLEMENT_PREAMBLE = (
    "ROW_CLASS LOW (mandatory): Land on green when AC met — merge the lane branch "
    "on green; silence is not a hold-merge gate. CDP review is a separate "
    "ticker-fired hop; the ticker then applies ALL review suggestions (SHOULD-FIX, "
    "nits, unused-code, adjacent) — do not page or come up. This dispatch is "
    "mechanical implement only.\n\n"
    "Friction {friction_id} [{category}] {owner}\n"
    "«{note}»\n"
    "{why_line}" + _DISPOSITION_EXIT + "\n"
)

_TRIO_SKETCH_PROMPT = (
    "ROW_CLASS TRIO — Sketch consult (mandatory): Bind the friction row into a "
    "Sketch → Mission Composer → Conductor path. Post your sketch/consult outcome "
    "on the house thread; do not implement in-repo. Ticker fires Mission Composer "
    "and Conductor after you STOP.\n\n"
    "Friction {friction_id} [{category}] {owner}\n"
    "«{note}»\n"
    "{why_line}" + _DISPOSITION_EXIT + "\n"
)


def row_bind_model(policy: dict[str, Any]) -> str:
    """Model for ROW_CLASS bind hops — ``policy.row_bind_model`` or ``cursor/grok-4.7``."""
    raw = str(policy.get("row_bind_model") or "").strip()
    if raw.startswith(("cursor/", "cdp/")):
        return raw
    return "cursor/grok-4.7"


def trio_sketch_model(policy: dict[str, Any]) -> str:
    """TRIO sketch consult when no ``todo:{slug}`` — default ``cdp/opus-5``."""
    raw = str(policy.get("trio_sketch_model") or "").strip()
    if raw.startswith(("cursor/", "cdp/")):
        return raw
    return "cdp/opus-5"


def _row_bind_model_knobs(policy: dict[str, Any], model: str) -> dict[str, str] | None:
    knobs = policy.get("row_bind_model_knobs")
    if isinstance(knobs, dict):
        return {str(k): str(v) for k, v in knobs.items()}
    bare = model.rsplit("/", 1)[-1] if model else ""
    if model.startswith("cursor/") and bare == "grok-4.7":
        return {"effort": "high", "fast": "false"}
    return None


def _wire_cursor_row_bind(body: dict[str, Any], policy: dict[str, Any], model: str) -> None:
    if not model.startswith("cursor/"):
        return
    body["seat"] = "cursor-sdk"
    body["lane"] = "B"
    mk = _row_bind_model_knobs(policy, model)
    if mk:
        body["model_knobs"] = mk


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
    model = row_bind_model(policy)
    body: dict[str, Any] = {
        "op": "generate",
        "model": model,
        "contract": "none",
        "prompt": prompt,
        "dispatch_thread_id": tape,
        "work_key": f"row-bind:{fid}:night-{current_night_id()}",
        "timeout_seconds": max_hop * 60 + 1800,
        "caller_agent": "liaison-ticker",
        "_row_bind": True,
        "_friction_id": fid,
    }
    _wire_cursor_row_bind(body, policy, model)
    return body


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
    # Night-scope the work_key so a still-open row cannot remint-cap the house (B1).
    low_key = f"row-low:{fid}:night-{current_night_id()}"
    return {
        "op": "generate",
        "seat": "cursor-sdk",
        "contract": "implement",
        "lane": "B",
        "source_ref": low_key,
        "work_key": low_key,
        "prompt": prompt,
        "dispatch_thread_id": tape,
        "timeout_seconds": max_hop * 60 + 1800,
        "caller_agent": "liaison-ticker",
        "_row_class": ROW_CLASS_LOW,
        "_friction_id": fid,
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
    model = trio_sketch_model(policy)
    body: dict[str, Any] = {
        "op": "generate",
        "model": model,
        "contract": "none",
        "prompt": prompt,
        "dispatch_thread_id": tape,
        "work_key": f"row-trio-sketch:{fid}:night-{current_night_id()}",
        "timeout_seconds": max_hop * 60 + 1800,
        "caller_agent": "liaison-ticker",
        "_row_class": ROW_CLASS_TRIO,
        "_friction_id": fid,
    }
    _wire_cursor_row_bind(body, policy, model)
    return body


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
        fid = str(friction.get("id") or "")
        body["_row_class"] = ROW_CLASS_TRIO
        body["_friction_id"] = fid
        exit_line = _DISPOSITION_EXIT.format(friction_id=fid)
        prior = str(body.get("prompt") or "").rstrip()
        body["prompt"] = f"{prior}\n{exit_line}" if prior else exit_line
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
    absorb_row_classes_from_digest(digest, state)
    fid = str(friction.get("id") or "")
    if row_class_fired(state, fid):
        _page_row_class_park(root_id, state, fid, ROW_CLASS_FIRED)
        return {"_row_class_fired": True, "_refused": ROW_CLASS_FIRED}
    latched = latched_row_class(state, fid)
    if latched:
        cls = latched["class"]
        if cls == ROW_CLASS_LOW:
            return build_low_implement_body(root_id, policy, friction, latched=latched)
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
        _page_row_class_park(root_id, state, fid, ROW_CLASS_HOLD)
        return {"_row_class_hold": True, "_refused": ROW_CLASS_HOLD}
    return build_row_bind_body(root_id, policy, friction)


def _page_row_class_park(
    root_id: str, state: dict[str, Any], friction_id: str, refused: str
) -> None:
    """Page once per fid+sentinel so FIRED/HOLD are designed stops, not silent parks."""
    if not friction_id:
        return
    store = dict(state.get("row_class_paged") or {})
    stamp = f"{refused}:{friction_id}"
    if stamp in store:
        return
    page_liaison(
        root_id,
        f"liaison {root_id} — {refused} {friction_id}",
        f"Sit leftover parked: {refused} on {friction_id}. "
        "Mark the row (--mark-friction) or the sit path stays held.",
    )
    store[stamp] = True
    state["row_class_paged"] = dict(list(store.items())[-200:])


__all__ = [
    "body_for_sit_friction",
    "build_low_implement_body",
    "build_row_bind_body",
    "build_trio_fire_body",
    "build_trio_sketch_body",
    "row_bind_model",
    "trio_sketch_model",
]

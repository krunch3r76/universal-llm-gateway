"""Successor message and dispatch payload assembly."""

from __future__ import annotations

from typing import Any

from bus_watch.doorbell import render_successor_wake
from bus_watch.fable_lock import current_night_id
from bus_watch.now_row import format_now_line, resolve_now_row

SUCCESSOR_MESSAGE_CAP = 2048


def build_successor_message(
    root_id: str,
    *,
    gear: str,
    row: str,
    seat: str = "cursor-sdk",
    tip_turn: int | None = None,
    tip_checkpoint_turn: int | None = None,
    spawn_signal_sources: list[str] | None = None,
    ring: str | None = None,
    extra_addresses: tuple[str, ...] = (),
    contract: str = "none",
    cap: int = SUCCESSOR_MESSAGE_CAP,
) -> str:
    """Inline resume-fence pull recipe for a headless liaison successor."""
    return render_successor_wake(
        root_id,
        gear=gear,
        row=row,
        seat=seat,
        tip_turn=tip_turn,
        tip_checkpoint_turn=tip_checkpoint_turn,
        spawn_signal_sources=spawn_signal_sources,
        ring=ring,
        extra_addresses=tuple(extra_addresses),
        contract=contract,
        cap=cap,
    )


def build_dispatch_body(
    root_id: str,
    policy: dict[str, Any],
    *,
    work_key: str | None = None,
    successor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the generate payload: successor model, night work_key, resume message."""
    max_hop = int(policy.get("max_hop_minutes") or 60)
    ctx = dict(successor_context or {})
    extras = ctx.get("extra_addresses") or policy.get("successor_extra_addresses") or ()
    seat = policy.get("successor_seat") or "cursor-sdk"
    contract = str(policy.get("successor_contract") or "none")
    message = build_successor_message(
        root_id,
        gear=str(ctx.get("gear") or policy.get("gear") or "1-fable-mvp"),
        row=str(ctx.get("row") or ""),
        seat=seat,
        tip_turn=ctx.get("tip_turn"),
        tip_checkpoint_turn=ctx.get("tip_checkpoint_turn"),
        spawn_signal_sources=list(ctx.get("spawn_signal_sources") or []),
        ring=ctx.get("ring") or policy.get("wake_ring"),
        extra_addresses=tuple(extras),
        contract=contract,
    )
    body: dict[str, Any] = {
        "op": "generate",
        "seat": seat,
        "contract": contract,
        "lane": "A",
        "model": policy.get("successor_model"),
        "message": message,
        "dispatch_thread_id": root_id,
        # Per-night work identity: GIW's remint cap counts admits per work_key, so a
        # root-wide key runs out after one night (a:33139 — hop 9 refused at seq 9 >
        # cap 8). Keying by night_id resets the sequence with the night, not by hand.
        "work_key": work_key or f"agent-bus:{root_id}:night-{current_night_id()}",
        "timeout_seconds": max_hop * 60 + 1800,
        "caller_agent": "liaison-ticker",
        **(
            {"cost_intent": policy["successor_cost_intent"]}
            if policy.get("successor_cost_intent")
            else {}
        ),
    }
    policy_knobs = policy.get("successor_model_knobs")
    if isinstance(policy_knobs, dict):
        body["model_knobs"] = {str(k): str(v) for k, v in policy_knobs.items()}
    else:
        model = str(policy.get("successor_model") or "")
        bare_id = model.rsplit("/", 1)[-1] if model else ""
        if bare_id == "grok-4.6":
            body["model_knobs"] = {"fast": "true"}
    return body


def _wire_submit_body(body: dict[str, Any]) -> dict[str, Any]:
    """Map local ``message`` to Stargate ``prompt``; drop generate-forbidden tags."""
    wired = dict(body)
    if wired.get("message") and not wired.get("prompt"):
        wired["prompt"] = wired.pop("message")
    wired.pop("tags", None)
    return wired


def successor_context_from_digest(
    digest: dict[str, Any],
    *,
    spawn_signal_sources: list[str] | None = None,
) -> dict[str, Any]:
    """Extract message bind fields from a digest snapshot; with no seat-bound
    row, a forcing friction is the row the successor is spawned for."""
    policy = digest.get("policy") or {}
    root = digest.get("root") or {}
    sources = spawn_signal_sources
    if sources is None:
        raw = digest.get("spawn_signal_sources")
        sources = list(raw) if isinstance(raw, list) else []
    tip_cp = root.get("tip_checkpoint_turn")
    raw_row, row_source = resolve_now_row(digest)
    if not raw_row:
        raw_row = str(root.get("last_subject") or "")
        row_source = "tip" if raw_row else "empty"
    return {
        "gear": policy.get("gear"),
        "row": format_now_line(raw_row, row_source, digest, omit_tip_prefix=True),
        "row_source": row_source,
        "tip_turn": root.get("turns"),
        "tip_checkpoint_turn": int(tip_cp) if tip_cp is not None else None,
        "spawn_signal_sources": sources,
        "ring": policy.get("wake_ring"),
    }

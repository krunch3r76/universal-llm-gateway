"""Pure fold for hop-before-checkpoint precedence (friction:43744 S2)."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

HopFoldState = dict[str, Any]

_EVENT_CHECKPOINT_SEALED = "checkpoint.sealed"
_EVENT_HOP_REQUESTED = "hop.requested"
_EVENT_HOP_COMPLETED = "hop.completed"

HOP_STATUS_READY = "ready"
HOP_STATUS_PENDING = "pending_checkpoint"

HELD_HOP_BEFORE_CHECKPOINT = "hop_before_checkpoint"
HELD_CHECKPOINT_PIN_MISMATCH = "checkpoint_pin_mismatch"
HELD_CHECKPOINT_REF_MISSING = "checkpoint_ref_missing"


def empty_hop_fold_state() -> HopFoldState:
    return {"checkpoints_by_uri": {}, "hops": {}}


def _checkpoint_pin(state: HopFoldState, uri: str) -> dict[str, Any] | None:
    return state.get("checkpoints_by_uri", {}).get(uri)


def _evaluate_hop_against_checkpoint(
    hop: dict[str, Any],
    checkpoint: dict[str, Any] | None,
) -> dict[str, Any]:
    uri = hop.get("required_checkpoint_uri")
    required_pin = hop.get("required_read_sha256")
    if not uri or not required_pin:
        hop = dict(hop)
        hop["hop_status"] = HOP_STATUS_PENDING
        hop["held_reason"] = HELD_CHECKPOINT_REF_MISSING
        hop["checkpoint_turn_id"] = None
        hop["checkpoint_read_sha256"] = None
        return hop
    if checkpoint is None:
        hop = dict(hop)
        hop["hop_status"] = HOP_STATUS_PENDING
        hop["held_reason"] = HELD_HOP_BEFORE_CHECKPOINT
        hop["checkpoint_turn_id"] = None
        hop["checkpoint_read_sha256"] = None
        return hop
    if str(checkpoint.get("read_sha256") or "") != str(required_pin):
        hop = dict(hop)
        hop["hop_status"] = HOP_STATUS_PENDING
        hop["held_reason"] = HELD_CHECKPOINT_PIN_MISMATCH
        hop["checkpoint_turn_id"] = checkpoint.get("turn_id")
        hop["checkpoint_read_sha256"] = checkpoint.get("read_sha256")
        return hop
    hop = dict(hop)
    hop["hop_status"] = HOP_STATUS_READY
    hop["held_reason"] = None
    hop["checkpoint_turn_id"] = checkpoint.get("turn_id")
    hop["checkpoint_read_sha256"] = checkpoint.get("read_sha256")
    return hop


def _refresh_hops_for_uri(state: HopFoldState, uri: str) -> None:
    hops = state.setdefault("hops", {})
    for hop_turn_id, hop in list(hops.items()):
        if hop.get("required_checkpoint_uri") != uri:
            continue
        checkpoint = _checkpoint_pin(state, uri)
        hops[hop_turn_id] = _evaluate_hop_against_checkpoint(hop, checkpoint)


def fold_hop_precedence(state: HopFoldState, event: dict[str, Any]) -> HopFoldState:
    """Apply one typed lifecycle event to in-memory hop precedence state."""
    next_state = deepcopy(state)
    event_type = str(event.get("event_type") or "")
    refs = dict(event.get("refs") or {})

    if event_type == _EVENT_CHECKPOINT_SEALED:
        uri = refs.get("uri")
        if uri:
            next_state.setdefault("checkpoints_by_uri", {})[uri] = {
                "turn_id": refs.get("turn_id"),
                "read_sha256": refs.get("read_sha256"),
                "cp_ordinal": refs.get("cp_ordinal"),
            }
            _refresh_hops_for_uri(next_state, uri)
        return next_state

    if event_type == _EVENT_HOP_REQUESTED:
        hop_turn_id = refs.get("turn_id")
        if hop_turn_id is None:
            return next_state
        hop_key = str(hop_turn_id)
        hop = {
            "hop_turn_id": hop_turn_id,
            "required_checkpoint_uri": refs.get("required_checkpoint_uri"),
            "required_read_sha256": refs.get("required_read_sha256"),
            "target_seat": refs.get("target_seat"),
        }
        uri = hop.get("required_checkpoint_uri")
        checkpoint = _checkpoint_pin(next_state, uri) if uri else None
        next_state.setdefault("hops", {})[hop_key] = _evaluate_hop_against_checkpoint(
            hop, checkpoint
        )
        return next_state

    if event_type == _EVENT_HOP_COMPLETED:
        # Precedence projection is unchanged on hop completion; S1 ledger may fold further.
        return next_state

    return next_state

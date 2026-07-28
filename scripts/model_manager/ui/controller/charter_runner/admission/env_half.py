"""ENV-half + residue-gate continuation for ``evaluate_root``."""

from __future__ import annotations

from datetime import datetime

from ..checkpoint_schema import ParsedCheckpoint
from ..env_predicates import EnvEvalContext, EnvironmentSnapshot, evaluate_env_half
from .body_gate import Decision, WindowKind, next_window_index
from .caps import CapStore
from .restart_pickup import next_pickup_is_restart_from_holder


def _env_skip(
    reason: str,
    root_id: str,
    *,
    predicate_id: str,
    checkpoint: dict,
    parsed: ParsedCheckpoint,
    window_kind: WindowKind = "worker",
) -> Decision:
    return Decision(
        False,
        reason,
        root_id,
        checkpoint=checkpoint,
        parsed=parsed,
        window_kind=window_kind,
        half="env",
        predicate_id=predicate_id,
    )


def _residue_skip(
    reason: str,
    root_id: str,
    *,
    checkpoint: dict,
    parsed: ParsedCheckpoint,
    window_kind: WindowKind,
    fingerprint: str,
) -> Decision:
    return Decision(
        False,
        reason,
        root_id,
        checkpoint=checkpoint,
        parsed=parsed,
        window_kind=window_kind,
        half="body",
        residue_fingerprint=fingerprint,
    )


def check_env_or_eligible(
    root_id: str,
    turns: list[dict],
    caps: CapStore,
    checkpoint: dict,
    parsed: ParsedCheckpoint,
    env_snapshot: EnvironmentSnapshot | None,
    admission_mode: str,
    *,
    now: datetime | None = None,
    window_kind: WindowKind = "worker",
) -> Decision:
    """Evaluate ENV predicates then residue thrash gate; return admit Decision."""
    next_window = next_window_index(turns)
    restart_shaped = any(
        next_pickup_is_restart_from_holder(item) for item in parsed.next_pickup
    )
    ctx = EnvEvalContext(
        restart_shaped=restart_shaped,
        admit_intent_orphan=caps.has_admit_intent(root_id, next_window),
    )
    env_skip = evaluate_env_half(env_snapshot, ctx, now=now)
    if env_skip is not None:
        return _env_skip(
            env_skip.reason,
            root_id,
            predicate_id=env_skip.predicate_id,
            checkpoint=checkpoint,
            parsed=parsed,
            window_kind=window_kind,
        )
    from ..residue_fingerprint import (
        ResidueRecord,
        evaluate_residue_gate,
        load_residue_record,
        save_residue_record,
    )

    if parsed.consult_pending:
        admission_mode = "consult"
    cp_body = str(checkpoint.get("body") or "")
    last = load_residue_record(root_id)
    gate = evaluate_residue_gate(
        checkpoint_body=cp_body,
        parsed=parsed,
        admission_mode=admission_mode,
        window_kind=window_kind,
        last=last,
        window_index=next_window,
    )
    if not gate.admit:
        save_residue_record(
            root_id,
            ResidueRecord(
                fingerprint=gate.fingerprint,
                witness=gate.witness,
                consecutive_skip_count=gate.consecutive_skip_count,
                w10_consumed=gate.w10_consumed,
                last_window_index=gate.last_window_index,
            ),
        )
        if gate.stop_root:
            caps.mark_failed(root_id, gate.reason)
        return _residue_skip(
            gate.reason,
            root_id,
            checkpoint=checkpoint,
            parsed=parsed,
            window_kind=window_kind,
            fingerprint=gate.fingerprint,
        )
    if gate.w10_consumed and last is not None:
        save_residue_record(
            root_id,
            ResidueRecord(
                fingerprint=gate.fingerprint,
                witness=gate.witness,
                consecutive_skip_count=0,
                w10_consumed=True,
                last_window_index=gate.last_window_index,
            ),
        )
    reason = "eligible_consult" if window_kind == "consult" else "eligible"
    return Decision(
        True,
        reason,
        root_id,
        checkpoint=checkpoint,
        parsed=parsed,
        window_kind=window_kind,
        residue_fingerprint=gate.fingerprint,
    )


__all__ = ["check_env_or_eligible"]

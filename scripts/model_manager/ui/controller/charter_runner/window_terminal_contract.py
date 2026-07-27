"""Sole import surface for charter-runner window terminals and arc derivation.

Stop vocabulary (CHECKPOINT / CONSULT_PENDING / BLOCKED / PACKAGING_DEFICIT) is
bound in autonomous-path-sim-charter § Stop vocabulary. ``density_triage`` on the
todo is the single authority for required arc — derived, never separately stamped.
"""

from __future__ import annotations

import re

from universal_logging import get_logger

from .checkpoint_parse import ParsedCheckpoint, parse_checkpoint

logger = get_logger(__name__)

# Bound stop vocabulary — subject-prefix match (case-insensitive).
WINDOW_TERMINALS: tuple[str, ...] = (
    "CHECKPOINT",
    "CONSULT_PENDING",
    "BLOCKED",
    "PACKAGING_DEFICIT",
)

CHECKPOINT_PREFIX = "CHECKPOINT"

ARC_MECHANICAL = "mechanical"
ARC_INVESTIGATE = "investigate"
ARC_R_ADMIT_REQUIRED = "r_admit_required"

_ARC_RANK: dict[str, int] = {
    ARC_MECHANICAL: 0,
    ARC_INVESTIGATE: 1,
    ARC_R_ADMIT_REQUIRED: 2,
}

_STOP_VOCAB_SECTION_RE = re.compile(
    r"^###\s+Stop vocabulary\b", re.MULTILINE | re.IGNORECASE
)
_STOP_VOCAB_ROW_RE = re.compile(r"^\|\s*`([^`]+)`", re.MULTILINE)
_WINDOW_TERMINAL_SPEC_ROWS = 4


def parse_stop_vocabulary_window_terminals(spec_text: str) -> tuple[str, ...]:
    """First four subject-prefix verbs from § Stop vocabulary table."""
    match = _STOP_VOCAB_SECTION_RE.search(spec_text or "")
    if not match:
        return ()
    tail = spec_text[match.end() :]
    verbs: list[str] = []
    for line in tail.splitlines():
        if line.startswith("### "):
            break
        row = _STOP_VOCAB_ROW_RE.match(line)
        if not row:
            continue
        raw = row.group(1).strip()
        verb = raw.split()[0].upper()
        if verb:
            verbs.append(verb)
        if len(verbs) >= _WINDOW_TERMINAL_SPEC_ROWS:
            break
    return tuple(verbs)


def is_tip_class(subject: str | None, *, body: str | None = None) -> bool:
    """True when the turn is a tip-class window terminal."""
    subj = str(subject or "").upper().strip()
    if subj and any(subj.startswith(verb) for verb in WINDOW_TERMINALS):
        return True
    if body and subj.startswith(CHECKPOINT_PREFIX):
        try:
            parsed = parse_checkpoint(body)
        except Exception:  # noqa: BLE001 — classify conservatively
            parsed = None
        if parsed is not None and parsed.consult_pending:
            return True
    return False


def terminal_verb(subject: str | None, *, body: str | None = None) -> str | None:
    """Return the matched stop verb, or None when the turn is not tip-class."""
    subj = str(subject or "").upper().strip()
    if body and subj.startswith(CHECKPOINT_PREFIX):
        try:
            parsed = parse_checkpoint(body)
        except Exception:  # noqa: BLE001
            parsed = None
        if parsed is not None and parsed.consult_pending:
            return "CONSULT_PENDING"
    if subj:
        for verb in WINDOW_TERMINALS:
            if subj.startswith(verb):
                return verb
    return None


def required_arc(density_triage: str | None) -> str:
    """Derive required arc from todo ``density_triage``; unknown ⇒ strictest."""
    triage = (density_triage or "").strip().lower()
    if triage == "mechanical":
        return ARC_MECHANICAL
    if triage == "recon_pending":
        return ARC_INVESTIGATE
    return ARC_R_ADMIT_REQUIRED


def admitted_arc(
    *,
    window_kind: str,
    admission_mode: str,
    consult_role: str | None,
    executor_lane: str,
) -> str:
    """Map the lane about to admit into arc vocabulary."""
    if window_kind == "consult" or admission_mode == "consult":
        if consult_role == "r_admit":
            return ARC_R_ADMIT_REQUIRED
        return ARC_INVESTIGATE
    if executor_lane == "implement" and admission_mode == "autonomous":
        return ARC_MECHANICAL
    return ARC_INVESTIGATE


def arc_is_weaker_than(admitted: str, required: str) -> bool:
    return _ARC_RANK.get(admitted, 0) < _ARC_RANK.get(required, 2)


def effective_required_arc(
    *,
    triage: str | None,
    executor_lane: str,
    consult_pending: bool,
    checkpoint_body: str,
) -> str:
    """Derive required arc from G-row lane; todo ``density_triage`` is secondary."""
    from .residue_fingerprint import consult_provenance_present

    if (
        executor_lane == "implement"
        and not consult_pending
        and consult_provenance_present(checkpoint_body)
    ):
        return ARC_MECHANICAL
    base = required_arc(triage)
    # G-row ``executor_lane: judgment`` (G1/G2 Grok densify) satisfies
    # ``judgment_required`` without escalating to G3 R-admit consult.
    if executor_lane == "judgment" and base == ARC_R_ADMIT_REQUIRED:
        return ARC_INVESTIGATE
    return base


def default_density_triage_lookup(todo_ref: str) -> str | None:
    try:
        from cortex_store.dispatch_ops.ops_entities import _op_entity_get

        ent = _op_entity_get(entity_id=todo_ref, intent="full")
    except Exception:  # noqa: BLE001 — offline tests / missing cortex
        return None
    if "error" in ent:
        return None
    attrs = ent.get("attributes") or {}
    if not isinstance(attrs, dict):
        return None
    raw = attrs.get("density_triage")
    return str(raw).strip() if raw is not None else None


def todo_refs_for_arc(parsed: ParsedCheckpoint) -> list[str]:
    refs: list[str] = []
    if parsed.source_ref:
        refs.append(parsed.source_ref.lower())
    for row in parsed.next_pickup:
        for match in re.finditer(
            r"\b((?:todo|plan|plan_phase):[a-z0-9][a-z0-9._-]*)", row, re.IGNORECASE
        ):
            refs.append(match.group(1).lower())
    seen: set[str] = set()
    ordered: list[str] = []
    for ref in refs:
        if ref not in seen:
            seen.add(ref)
            ordered.append(ref)
    return ordered


# Back-compat alias — importers migrate to is_tip_class.
is_checkpoint_class = is_tip_class


async def after_window_terminal_harvested(
    *,
    root_id: str,
    window_index: int,
    checkpoint_turn: int,
    checkpoint_subject: str,
    checkpoint_body: str,
    worker_turns: list[dict],
    worker_closed: bool | None,
    gate_bypass_count: int,
) -> None:
    """Post-close hook: friction audit → G3 mint → reconcile → conveyor enroll."""
    from cortex_store.dispatch_ops._friction_enqueue import (
        mint_friction_followon,
        mint_repair_todo,
        reconcile_charter_frictions,
        todo_exists_for_friction,
    )
    from cortex_store.dispatch_ops.ops_assertions import _op_frictions
    from cortex_store.dispatch_ops.ops_assertions_update import _op_assertion_get

    from scripts.model_manager import observation_event as events

    from . import bus_client, conveyor, frictions_window_audit

    _ = checkpoint_turn  # bound in contract signature for closeout correlation

    closeout_status = bus_client.closeout_status_from_turns(worker_turns)
    audit = frictions_window_audit.audit_window_frictions(
        checkpoint_body=checkpoint_body,
        root_id=root_id,
        window_index=window_index,
        assertion_get=lambda aid: _op_assertion_get(assertion_id=aid),
        frictions=_op_frictions,
        worker_closeout_status=closeout_status,
        checkpoint_subject=checkpoint_subject,
        worker_closed=worker_closed,
        gate_bypass_count=gate_bypass_count,
        worker_turns=worker_turns,
    )
    if not audit.applicable:
        await events.emit_manage_charter_tick_frictions_audit_not_applicable(
            root=root_id,
            window_index=window_index,
            reason=audit.not_applicable_reason or "not_applicable",
        )
        try:
            reconcile_charter_frictions(root_id)
        except Exception:  # noqa: BLE001
            logger.exception("charter-runner friction reconcile sweep failed")
        return

    if audit.audit_failed:
        await events.emit_manage_charter_tick_frictions_audit_failed(
            root=root_id,
            window_index=window_index,
            failure_class=audit.audit_failure_class or "unknown",
            non_actionable_rate=audit.non_actionable_rate,
        )
        try:
            mint_repair_todo(
                root_id=root_id,
                window_index=window_index,
                audit_failure_class=audit.audit_failure_class or "unknown",
            )
        except Exception:  # noqa: BLE001
            logger.exception("charter-runner repair todo mint failed")
    else:
        await events.emit_manage_charter_tick_frictions_audit_passed(
            root=root_id,
            window_index=window_index,
            non_actionable_rate=audit.non_actionable_rate,
        )

    if audit.uncited_ids:
        await events.emit_manage_charter_tick_frictions_filed_uncited(
            root=root_id,
            window_index=window_index,
            uncited_ids=sorted(audit.uncited_ids),
        )

    if audit.ceremonial_suspected:
        await events.emit_manage_charter_tick_frictions_ceremonial_suspected(
            root=root_id,
            window_index=window_index,
            non_actionable_rate=audit.non_actionable_rate,
        )

    for row in audit.resolved_actionable_rows:
        try:
            got = _op_assertion_get(assertion_id=row.assertion_id)
            if "error" not in got:
                slug = mint_friction_followon(got, root_id=root_id)
                if slug:
                    audit.enqueued_ids.add(row.assertion_id)
        except Exception:  # noqa: BLE001
            logger.exception(
                "charter-runner friction enqueue failed id=%s", row.assertion_id
            )

    for fid in audit.uncited_ids:
        try:
            got = _op_assertion_get(assertion_id=fid)
            if "error" not in got:
                slug = mint_friction_followon(got, root_id=root_id)
                if slug:
                    audit.enqueued_ids.add(fid)
        except Exception:  # noqa: BLE001
            logger.exception("charter-runner uncited friction enqueue failed id=%s", fid)

    try:
        reconcile_charter_frictions(root_id)
    except Exception:  # noqa: BLE001
        logger.exception("charter-runner friction reconcile sweep failed")

    try:
        detail = await bus_client.fetch_thread(root_id)
        tags = list(detail.get("tags") or [])
        friction_resp = _op_frictions(
            charter_root=root_id,
            superseded=False,
            limit=200,
            intent="full",
        )
        friction_items = [
            item
            for item in friction_resp.get("items") or []
            if isinstance(item, dict) and todo_exists_for_friction(int(item["id"]))
        ]
        await conveyor.enroll_rows(
            root_id=root_id,
            root_tags=tags,
            friction_rows=friction_items,
        )
    except Exception as exc:  # noqa: BLE001 — surface; do not abort harvest closeout
        logger.exception("charter-runner conveyor enroll failed root=%s", root_id)
        try:
            from scripts.model_manager import observation_event_conveyor as conv_events

            await conv_events.emit_manage_charter_conveyor_enroll_failed(
                root=root_id,
                window_index=window_index,
                error=f"{type(exc).__name__}: {exc}",
                minted_count=len(audit.enqueued_ids),
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "charter-runner failed emitting conveyor enroll_failed root=%s",
                root_id,
            )
        try:
            mint_repair_todo(
                root_id=root_id,
                window_index=window_index,
                audit_failure_class="conveyor_enroll_failed",
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "charter-runner conveyor enroll repair-todo mint failed root=%s",
                root_id,
            )


__all__ = [
    "ARC_INVESTIGATE",
    "ARC_MECHANICAL",
    "ARC_R_ADMIT_REQUIRED",
    "CHECKPOINT_PREFIX",
    "WINDOW_TERMINALS",
    "admitted_arc",
    "after_window_terminal_harvested",
    "arc_is_weaker_than",
    "default_density_triage_lookup",
    "effective_required_arc",
    "is_checkpoint_class",
    "is_tip_class",
    "parse_stop_vocabulary_window_terminals",
    "required_arc",
    "terminal_verb",
    "todo_refs_for_arc",
]

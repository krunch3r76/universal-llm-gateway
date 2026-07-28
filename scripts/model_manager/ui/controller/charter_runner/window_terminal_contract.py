"""Sole import surface for charter-runner window terminals and arc derivation.

Stop vocabulary (CHECKPOINT / CONSULT_PENDING / BLOCKED / PACKAGING_DEFICIT) is
bound in autonomous-path-sim-charter § Stop vocabulary. ``density_triage`` on the
todo is the single authority for required arc — derived, never separately stamped.
"""

from __future__ import annotations

import re

from universal_logging import get_logger

from .checkpoint_schema import ParsedCheckpoint, parse_checkpoint
from .window_terminal_harvest import after_window_terminal_harvested

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

_PICKUP_DENSITY_RE = re.compile(r"\bdensity=(\w+)", re.IGNORECASE)
_IMPLEMENT_READY_RE = re.compile(r"\bimplement_ready=", re.IGNORECASE)
_R_ADMIT_DONE_RE = re.compile(r"\bR-admit\b", re.IGNORECASE)

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
    parsed: ParsedCheckpoint | None = None,
) -> str:
    """Map the lane about to admit into arc vocabulary."""
    if window_kind == "consult" or admission_mode == "consult":
        if consult_role == "r_admit":
            return ARC_R_ADMIT_REQUIRED
        return ARC_INVESTIGATE
    if executor_lane == "implement" and (
        admission_mode == "autonomous" or implement_ready_declared(parsed)
    ):
        return ARC_MECHANICAL
    return ARC_INVESTIGATE


def density_triage_from_pickup(parsed: ParsedCheckpoint) -> str | None:
    """``density=`` on a Next-pickup row overrides todo ``density_triage`` for arc."""
    for row in parsed.next_pickup:
        match = _PICKUP_DENSITY_RE.search(row)
        if match:
            return match.group(1).strip().lower()
    return None


def implement_ready_declared(parsed: ParsedCheckpoint | None) -> bool:
    """True when Next-pickup names ``implement_ready=`` (post-R-admit implement)."""
    if parsed is None:
        return False
    return any(_IMPLEMENT_READY_RE.search(row) for row in parsed.next_pickup)


def r_admit_step_done(parsed: ParsedCheckpoint | None) -> bool:
    """True when Steps show a completed R-admit row (G3 satisfied)."""
    if parsed is None:
        return False
    for step in parsed.steps:
        if step.status != "done":
            continue
        if _R_ADMIT_DONE_RE.search(step.title):
            return True
    return False


def arc_is_weaker_than(admitted: str, required: str) -> bool:
    return _ARC_RANK.get(admitted, 0) < _ARC_RANK.get(required, 2)


def effective_required_arc(
    *,
    triage: str | None,
    executor_lane: str,
    consult_pending: bool,
    checkpoint_body: str,
    parsed: ParsedCheckpoint | None = None,
) -> str:
    """Derive required arc from G-row lane; todo ``density_triage`` is secondary."""
    from .residue_fingerprint import consult_provenance_present

    if executor_lane == "implement" and not consult_pending:
        if consult_provenance_present(checkpoint_body):
            return ARC_MECHANICAL
        if r_admit_step_done(parsed) or implement_ready_declared(parsed):
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

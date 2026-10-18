"""In-process job table. One row per job id. Derived sets are row fields.

Import-time asserts compare each derived set to a handwritten literal.
A set computed only from rows, with no literal, is not the guard.
Cortex is not consulted. ``density_triage=mechanical`` is not a job id.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

_GENERATE = "generate"
_TO_THREAD = "to_thread"
_HANDOFF = "handoff"
_CURSOR_AUTO = "cursor_auto"

_INLINE_HANDLES = frozenset({"prompt", "packet_path", "sidecar_ref"})
_SOURCE_REF_HANDLES = frozenset({"source_ref"})


@dataclass(frozen=True, slots=True)
class JobRecord:
    """One job id and the fields the wire used to scatter across contract sets."""

    name: str
    admitted_ops: frozenset[str]
    stop_after_allowed: bool
    cost_risk_warn: bool
    recon_owed: bool
    closeout_subject: str
    resume_root_ok: bool
    ac_observed: bool
    ledger_continuation: bool
    role_turn: bool
    handle_set: frozenset[str]
    source_ref_required: bool
    inline_prompt_refused: bool
    posture_skip: bool
    hypothesize_on: bool
    harness_stack_skip: bool
    delegation_tier: Literal["A", "B", "C"]

    @property
    def registry_ref(self) -> str:
        return f"job_vocab:{self.name}"

    @property
    def delivery_role(self) -> str:
        """check-review delivers as reviewer. Every other job leaves it empty.

        Spec step 3: check-review sets delivery_role to reviewer. freeform,
        code-review, and delivery-review copy the old none row with the
        field empty. Breaks when a generate return reads a missing attribute
        and drops resolved_job.
        """
        return "reviewer" if self.name == "check-review" else ""

    @property
    def source_ref_refused(self) -> bool:
        return not self.source_ref_required


def _inline(
    name: str,
    admitted: frozenset[str],
    *,
    stop_after_allowed: bool,
    cost_risk_warn: bool = False,
    recon_owed: bool = False,
    role_turn: bool = False,
    posture_skip: bool = False,
    hypothesize_on: bool = False,
    harness_stack_skip: bool = False,
    delegation_tier: Literal["A", "B", "C"] = "B",
) -> JobRecord:
    return JobRecord(
        name=name,
        admitted_ops=admitted,
        stop_after_allowed=stop_after_allowed,
        cost_risk_warn=cost_risk_warn,
        recon_owed=recon_owed,
        closeout_subject="",
        resume_root_ok=False,
        ac_observed=False,
        ledger_continuation=False,
        role_turn=role_turn,
        handle_set=_INLINE_HANDLES,
        source_ref_required=False,
        inline_prompt_refused=False,
        posture_skip=posture_skip,
        hypothesize_on=hypothesize_on,
        harness_stack_skip=harness_stack_skip,
        delegation_tier=delegation_tier,
    )


def _source_ref(
    name: str,
    admitted: frozenset[str],
    *,
    posture_skip: bool = False,
    hypothesize_on: bool = False,
    delegation_tier: Literal["A", "B", "C"] = "B",
) -> JobRecord:
    return JobRecord(
        name=name,
        admitted_ops=admitted,
        stop_after_allowed=True,
        cost_risk_warn=False,
        recon_owed=False,
        closeout_subject="",
        resume_root_ok=False,
        ac_observed=False,
        ledger_continuation=False,
        role_turn=False,
        handle_set=_SOURCE_REF_HANDLES,
        source_ref_required=True,
        inline_prompt_refused=True,
        posture_skip=posture_skip,
        hypothesize_on=hypothesize_on,
        harness_stack_skip=False,
        delegation_tier=delegation_tier,
    )


_G = frozenset({_GENERATE})
_GT = frozenset({_GENERATE, _TO_THREAD})
_GTH = frozenset({_GENERATE, _TO_THREAD, _HANDOFF})
_ALL_WIRE = frozenset({_GENERATE, _TO_THREAD, _HANDOFF, _CURSOR_AUTO})
_G_HAND = frozenset({_GENERATE, _HANDOFF})
_G_HAND_AUTO = frozenset({_GENERATE, _HANDOFF, _CURSOR_AUTO})
_G_AUTO = frozenset({_GENERATE, _CURSOR_AUTO})
_AUTO = frozenset({_CURSOR_AUTO})

# freeform copies the old ``none`` row, including hypothesize_on.
# code-review / delivery-review copy that row without hypothesize_on.
# check-review copies that row and sets role_turn.
# mechanical copies the old pure-mechanical row (stop_after was not forbidden).
# confer copies the old consult row.
JOB_RECORDS: tuple[JobRecord, ...] = (
    _inline(
        "freeform",
        _ALL_WIRE,
        stop_after_allowed=False,
        recon_owed=True,
        hypothesize_on=True,
        harness_stack_skip=True,
        delegation_tier="B",
    ),
    _inline(
        "bare",
        _ALL_WIRE,
        stop_after_allowed=False,
        recon_owed=False,
        posture_skip=True,
        hypothesize_on=False,
        harness_stack_skip=False,
        delegation_tier="C",
    ),
    _inline(
        "mechanical",
        _ALL_WIRE,
        stop_after_allowed=True,
        cost_risk_warn=True,
        posture_skip=True,
        delegation_tier="C",
    ),
    _source_ref("sketch", _ALL_WIRE, hypothesize_on=True, delegation_tier="B"),
    _source_ref("implement", _ALL_WIRE, posture_skip=True, delegation_tier="C"),
    _source_ref("wrap", _G_AUTO, delegation_tier="C"),
    _source_ref("conductor", _G_AUTO, hypothesize_on=True, delegation_tier="A"),
    _inline(
        "code-review",
        _G,
        stop_after_allowed=False,
        recon_owed=True,
        delegation_tier="B",
    ),
    _inline(
        "delivery-review",
        _G,
        stop_after_allowed=False,
        recon_owed=True,
        delegation_tier="B",
    ),
    _inline(
        "check-review",
        _G,
        stop_after_allowed=False,
        recon_owed=True,
        role_turn=True,
        delegation_tier="B",
    ),
    _inline(
        "confer",
        _G_HAND_AUTO,
        stop_after_allowed=True,
        hypothesize_on=True,
        delegation_tier="B",
    ),
    _inline(
        "investigate",
        _G_AUTO,
        stop_after_allowed=True,
        recon_owed=True,
        delegation_tier="B",
    ),
    _inline(
        "answer",
        _AUTO,
        stop_after_allowed=True,
        posture_skip=True,
        delegation_tier="B",
    ),
    _inline(
        "ask",
        _AUTO,
        stop_after_allowed=True,
        posture_skip=True,
        delegation_tier="B",
    ),
    _inline("verify", _AUTO, stop_after_allowed=True, delegation_tier="B"),
    _inline(
        "execute",
        _AUTO,
        stop_after_allowed=True,
        posture_skip=True,
        delegation_tier="C",
    ),
    _inline(
        "propagate",
        _AUTO,
        stop_after_allowed=True,
        posture_skip=True,
        delegation_tier="C",
    ),
    _inline("seed", _AUTO, stop_after_allowed=True, delegation_tier="B"),
    _inline("recon", _AUTO, stop_after_allowed=True, delegation_tier="B"),
)

BY_NAME: dict[str, JobRecord] = {record.name: record for record in JOB_RECORDS}


def _derived(predicate) -> frozenset[str]:
    return frozenset(record.name for record in JOB_RECORDS if predicate(record))


def _admits(op: str):
    return lambda record: op in record.admitted_ops


GENERATE_ADMITTED_JOBS = _derived(_admits(_GENERATE))
TO_THREAD_ADMITTED_JOBS = _derived(_admits(_TO_THREAD))
HANDOFF_ADMITTED_JOBS = _derived(_admits(_HANDOFF))
CURSOR_AUTO_ADMITTED_JOBS = _derived(_admits(_CURSOR_AUTO))
SOURCE_REF_JOBS = _derived(lambda record: record.source_ref_required)
INLINE_ONLY_JOBS = _derived(lambda record: record.source_ref_refused)
POSTURE_SKIP_JOBS = _derived(lambda record: record.posture_skip)
HYPOTHESIZE_ON_JOBS = _derived(lambda record: record.hypothesize_on)
HARNESS_STACK_SKIP_JOBS = _derived(lambda record: record.harness_stack_skip)
ROLE_TURN_JOBS = _derived(lambda record: record.role_turn)

# Handwritten comparands. AC9. Not computed from rows.
_GENERATE_ADMITTED_LITERAL = frozenset(
    {
        "bare",
        "freeform",
        "mechanical",
        "sketch",
        "implement",
        "wrap",
        "conductor",
        "code-review",
        "delivery-review",
        "check-review",
        "confer",
        "investigate",
    }
)
_TO_THREAD_ADMITTED_LITERAL = frozenset(
    {"bare", "freeform", "mechanical", "sketch", "implement"}
)
_HANDOFF_ADMITTED_LITERAL = frozenset(
    {"bare", "freeform", "mechanical", "sketch", "implement", "confer"}
)
_CURSOR_AUTO_ADMITTED_LITERAL = frozenset(
    {
        "answer",
        "bare",
        "confer",
        "ask",
        "investigate",
        "implement",
        "verify",
        "execute",
        "propagate",
        "seed",
        "recon",
        "freeform",
        "mechanical",
        "conductor",
        "wrap",
        "sketch",
    }
)
_SOURCE_REF_LITERAL = frozenset({"implement", "sketch", "wrap", "conductor"})
_POSTURE_SKIP_LITERAL = frozenset(
    {"bare", "implement", "mechanical", "propagate", "execute", "answer", "ask"}
)
_HYPOTHESIZE_LITERAL = frozenset({"confer", "sketch", "conductor", "freeform"})
_HARNESS_STACK_SKIP_LITERAL = frozenset({"freeform"})
_ROLE_TURN_LITERAL = frozenset({"check-review"})

assert GENERATE_ADMITTED_JOBS == _GENERATE_ADMITTED_LITERAL
assert TO_THREAD_ADMITTED_JOBS == _TO_THREAD_ADMITTED_LITERAL
assert HANDOFF_ADMITTED_JOBS == _HANDOFF_ADMITTED_LITERAL
assert CURSOR_AUTO_ADMITTED_JOBS == _CURSOR_AUTO_ADMITTED_LITERAL
assert SOURCE_REF_JOBS == _SOURCE_REF_LITERAL
assert POSTURE_SKIP_JOBS == _POSTURE_SKIP_LITERAL
assert HYPOTHESIZE_ON_JOBS == _HYPOTHESIZE_LITERAL
assert HARNESS_STACK_SKIP_JOBS == _HARNESS_STACK_SKIP_LITERAL
assert ROLE_TURN_JOBS == _ROLE_TURN_LITERAL
assert INLINE_ONLY_JOBS.isdisjoint(SOURCE_REF_JOBS)

TIER_A_ORCHESTRATOR_JOBS = frozenset({"conductor"})
TIER_B_JUDGMENT_RECON_JOBS = frozenset(
    {
        "freeform",
        "sketch",
        "confer",
        "investigate",
        "code-review",
        "delivery-review",
        "check-review",
        "answer",
        "ask",
        "verify",
        "seed",
        "recon",
    }
)
TIER_C_LEAF_JOBS = frozenset(
    {"bare", "mechanical", "implement", "wrap", "execute", "propagate"}
)

assert TIER_A_ORCHESTRATOR_JOBS.isdisjoint(TIER_B_JUDGMENT_RECON_JOBS)
assert TIER_A_ORCHESTRATOR_JOBS.isdisjoint(TIER_C_LEAF_JOBS)
assert TIER_B_JUDGMENT_RECON_JOBS.isdisjoint(TIER_C_LEAF_JOBS)
assert (
    TIER_A_ORCHESTRATOR_JOBS | TIER_B_JUDGMENT_RECON_JOBS | TIER_C_LEAF_JOBS
) == frozenset(BY_NAME)
assert all(
    record.delegation_tier == "A"
    for name, record in BY_NAME.items()
    if name in TIER_A_ORCHESTRATOR_JOBS
)
assert all(
    record.delegation_tier == "B"
    for name, record in BY_NAME.items()
    if name in TIER_B_JUDGMENT_RECON_JOBS
)
assert all(
    record.delegation_tier == "C"
    for name, record in BY_NAME.items()
    if name in TIER_C_LEAF_JOBS
)


def job_record(name: str) -> JobRecord | None:
    return BY_NAME.get(name)


def registry_ref_for(name: str) -> str:
    record = BY_NAME.get(name)
    if record is None:
        return "job_vocab:unresolved"
    return record.registry_ref

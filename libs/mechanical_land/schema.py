"""Mechanical-land journal schema — events, payloads, and folded run state."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

JOURNAL_EVENT_NAMES: frozenset[str] = frozenset(
    {
        "run_birthed",
        "policy_stamped",
        "transition_decided",
        "leg_admitted",
        "leg_admit_failed",
        "leg_closed",
        "gate_ran",
        "review_admitted",
        "review_verdict",
        "review_fallback_admitted",
        "land_attempted",
        "restart_planned",
        "restart_issued",
        "probe_ran",
        "closed",
        "evidence_harvested",
        "ruling_folded",
        "ruling_ignored",
        "blocked",
        "resumed",
        "flow_forked",
        "flow_joined",
    }
)


class JournalForkRefused(Exception):  # noqa: N818 — spec §4.2 name
    """Raised when ``prev_seq`` does not match the folded journal tip."""


class UnknownJournalEvent(Exception):  # noqa: N818 — spec §4.2 fold branch
    """Raised when the journal contains an event name outside §4.2."""


class GateVerdict(StrEnum):
    GREEN = "green"
    RED = "red"
    INFRA_ERROR = "infra_error"


class ReviewVerdict(StrEnum):
    APPROVE = "APPROVE"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    UNBOUND = "unbound"


@dataclass(frozen=True)
class RunBirthedPayload:
    kind: str
    source_ref: str
    policy_sha256: str
    caller: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunBirthedPayload:
        return cls(
            kind=str(data["kind"]),
            source_ref=str(data["source_ref"]),
            policy_sha256=str(data["policy_sha256"]),
            caller=str(data["caller"]),
        )


@dataclass(frozen=True)
class PolicyStampedPayload:
    policy: dict[str, Any]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PolicyStampedPayload:
        policy = data.get("policy")
        if not isinstance(policy, dict):
            raise ValueError("policy_stamped requires policy dict")
        return cls(policy=dict(policy))


@dataclass(frozen=True)
class TransitionDecidedPayload:
    from_step: str
    to_step: str
    attempt: int
    reason: str
    pipeline_execution_id: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TransitionDecidedPayload:
        return cls(
            from_step=str(data["from_step"]),
            to_step=str(data["to_step"]),
            attempt=int(data["attempt"]),
            reason=str(data.get("reason", "")),
            pipeline_execution_id=(
                str(data["pipeline_execution_id"])
                if data.get("pipeline_execution_id")
                else None
            ),
        )


@dataclass(frozen=True)
class LegAdmittedPayload:
    step_id: str
    attempt: int
    dispatch_id: str
    execution_id: str
    model: str
    contract: str
    lane: str
    sdk_mode: str | None
    packet_sha256: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LegAdmittedPayload:
        sdk = data.get("sdk_mode")
        return cls(
            step_id=str(data["step_id"]),
            attempt=int(data["attempt"]),
            dispatch_id=str(data["dispatch_id"]),
            execution_id=str(data["execution_id"]),
            model=str(data["model"]),
            contract=str(data["contract"]),
            lane=str(data["lane"]),
            sdk_mode=str(sdk) if sdk is not None else None,
            packet_sha256=str(data["packet_sha256"]),
        )


@dataclass(frozen=True)
class LegAdmitFailedPayload:
    status_code: int
    error: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LegAdmitFailedPayload:
        return cls(status_code=int(data["status_code"]), error=str(data["error"]))


@dataclass(frozen=True)
class LegClosedPayload:
    dispatch_id: str
    closeout_status: str
    work_outcome: str
    head_sha: str
    branch: str
    branch_point: str
    files_modified: tuple[str, ...] = ()
    files_created: tuple[str, ...] = ()
    files_deleted: tuple[str, ...] = ()
    verification_summary: str | None = None
    sidecar_ref: str | None = None
    forbidden_git_ops: tuple[dict[str, Any], ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LegClosedPayload:
        return cls(
            dispatch_id=str(data["dispatch_id"]),
            closeout_status=str(data["closeout_status"]),
            work_outcome=str(data["work_outcome"]),
            head_sha=str(data["head_sha"]),
            branch=str(data["branch"]),
            branch_point=str(data["branch_point"]),
            files_modified=_tuple_str(data.get("files_modified")),
            files_created=_tuple_str(data.get("files_created")),
            files_deleted=_tuple_str(data.get("files_deleted")),
            verification_summary=(
                str(data["verification_summary"])
                if data.get("verification_summary") is not None
                else None
            ),
            sidecar_ref=(
                str(data["sidecar_ref"])
                if data.get("sidecar_ref") is not None
                else None
            ),
            forbidden_git_ops=_tuple_dict(data.get("forbidden_git_ops")),
        )


@dataclass(frozen=True)
class RuffGateSlice:
    exit: int
    files: tuple[str, ...] = ()
    output_sha256: str | None = None
    tail: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> RuffGateSlice | None:
        if not data:
            return None
        return cls(
            exit=int(data["exit"]),
            files=_tuple_str(data.get("files")),
            output_sha256=(
                str(data["output_sha256"]) if data.get("output_sha256") else None
            ),
            tail=str(data["tail"]) if data.get("tail") is not None else None,
        )


@dataclass(frozen=True)
class PytestGateSlice:
    exit: int
    selected: tuple[str, ...] = ()
    output_sha256: str | None = None
    tail: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> PytestGateSlice | None:
        if not data:
            return None
        return cls(
            exit=int(data["exit"]),
            selected=_tuple_str(data.get("selected")),
            output_sha256=(
                str(data["output_sha256"]) if data.get("output_sha256") else None
            ),
            tail=str(data["tail"]) if data.get("tail") is not None else None,
        )


@dataclass(frozen=True)
class PipelineValidateSlice:
    ids: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> PipelineValidateSlice | None:
        if not data:
            return None
        return cls(
            ids=_tuple_str(data.get("ids")),
            errors=_tuple_str(data.get("errors")),
        )


@dataclass(frozen=True)
class GateRanPayload:
    attempt: int
    head_sha: str
    worktree: str
    verdict: GateVerdict
    ruff: RuffGateSlice | None = None
    pytest: PytestGateSlice | None = None
    pipeline_validate: PipelineValidateSlice | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GateRanPayload:
        return cls(
            attempt=int(data["attempt"]),
            head_sha=str(data["head_sha"]),
            worktree=str(data["worktree"]),
            verdict=GateVerdict(str(data["verdict"])),
            ruff=RuffGateSlice.from_dict(data.get("ruff")),
            pytest=PytestGateSlice.from_dict(data.get("pytest")),
            pipeline_validate=PipelineValidateSlice.from_dict(
                data.get("pipeline_validate")
            ),
        )


@dataclass(frozen=True)
class ReviewAdmittedPayload:
    dispatch_id: str
    model: str
    range: str
    head: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReviewAdmittedPayload:
        return cls(
            dispatch_id=str(data["dispatch_id"]),
            model=str(data["model"]),
            range=str(data["range"]),
            head=str(data["head"]),
        )


@dataclass(frozen=True)
class ReviewVerdictPayload:
    tip_sha: str
    verdict: ReviewVerdict
    review_dispatch_id: str
    bind_status: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReviewVerdictPayload:
        return cls(
            tip_sha=str(data["tip_sha"]),
            verdict=ReviewVerdict(str(data["verdict"])),
            review_dispatch_id=str(data["review_dispatch_id"]),
            bind_status=str(data.get("bind_status", "")),
        )


@dataclass(frozen=True)
class ReviewFallbackAdmittedPayload:
    model: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReviewFallbackAdmittedPayload:
        return cls(model=str(data["model"]))


@dataclass(frozen=True)
class LandAttemptedPayload:
    tip_sha: str
    before_sha: str
    after_sha: str
    landed: bool
    refuse_reason: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LandAttemptedPayload:
        reason = data.get("refuse_reason")
        return cls(
            tip_sha=str(data["tip_sha"]),
            before_sha=str(data["before_sha"]),
            after_sha=str(data["after_sha"]),
            landed=bool(data["landed"]),
            refuse_reason=str(reason) if reason is not None else None,
        )


@dataclass(frozen=True)
class RestartPlannedPayload:
    land_sha: str
    restart_set: tuple[str, ...]
    porcelain_block: tuple[str, ...] = ()
    unmapped: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RestartPlannedPayload:
        return cls(
            land_sha=str(data["land_sha"]),
            restart_set=_tuple_str(data.get("restart_set")),
            porcelain_block=_tuple_str(data.get("porcelain_block")),
            unmapped=_tuple_str(data.get("unmapped")),
        )


@dataclass(frozen=True)
class RestartIssuedPayload:
    service: str
    intent_id: str
    state: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RestartIssuedPayload:
        return cls(
            service=str(data["service"]),
            intent_id=str(data["intent_id"]),
            state=str(data["state"]),
        )


@dataclass(frozen=True)
class ProbeRanPayload:
    service: str
    observed_code_version: str
    live: bool

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProbeRanPayload:
        return cls(
            service=str(data["service"]),
            observed_code_version=str(data["observed_code_version"]),
            live=bool(data["live"]),
        )


@dataclass(frozen=True)
class ClosedPayload:
    todo_close_result: str
    friction_close_result: list[str] | tuple[str, ...]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClosedPayload:
        raw = data.get("friction_close_result", [])
        if isinstance(raw, list):
            friction = tuple(str(x) for x in raw)
        elif isinstance(raw, tuple):
            friction = tuple(str(x) for x in raw)
        else:
            friction = ()
        return cls(
            todo_close_result=str(data["todo_close_result"]),
            friction_close_result=friction,
        )


@dataclass(frozen=True)
class EvidenceHarvestedPayload:
    sidecar_ref: str
    sha256: str
    status: str
    excerpt_sha256: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceHarvestedPayload:
        return cls(
            sidecar_ref=str(data["sidecar_ref"]),
            sha256=str(data["sha256"]),
            status=str(data["status"]),
            excerpt_sha256=str(data["excerpt_sha256"]),
        )


@dataclass(frozen=True)
class RulingFoldedPayload:
    verb: str
    step: str | None
    reason: str | None
    bus_thread: str
    turn: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RulingFoldedPayload:
        step = data.get("step")
        reason = data.get("reason")
        return cls(
            verb=str(data["verb"]),
            step=str(step) if step is not None else None,
            reason=str(reason) if reason is not None else None,
            bus_thread=str(data["bus_thread"]),
            turn=int(data["turn"]),
        )


@dataclass(frozen=True)
class RulingIgnoredPayload:
    bus_thread: str
    turn: int
    why: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RulingIgnoredPayload:
        return cls(
            bus_thread=str(data["bus_thread"]),
            turn=int(data["turn"]),
            why=str(data["why"]),
        )


@dataclass(frozen=True)
class BlockedPayload:
    step_id: str
    attempt: int
    reason_code: str
    detail: str
    posted_turn: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BlockedPayload:
        posted = data.get("posted_turn")
        return cls(
            step_id=str(data["step_id"]),
            attempt=int(data["attempt"]),
            reason_code=str(data["reason_code"]),
            detail=str(data["detail"]),
            posted_turn=int(posted) if posted is not None else None,
        )


@dataclass(frozen=True)
class ResumedPayload:
    by_ruling_seq: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResumedPayload:
        return cls(by_ruling_seq=int(data["by_ruling_seq"]))


@dataclass(frozen=True)
class FlowForkedPayload:
    from_seq: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FlowForkedPayload:
        return cls(from_seq=int(data["from_seq"]))


@dataclass(frozen=True)
class FlowJoinedPayload:
    survivor_flow: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FlowJoinedPayload:
        return cls(survivor_flow=str(data["survivor_flow"]))


PAYLOAD_TYPES: dict[str, type[Any]] = {
    "run_birthed": RunBirthedPayload,
    "policy_stamped": PolicyStampedPayload,
    "transition_decided": TransitionDecidedPayload,
    "leg_admitted": LegAdmittedPayload,
    "leg_admit_failed": LegAdmitFailedPayload,
    "leg_closed": LegClosedPayload,
    "gate_ran": GateRanPayload,
    "review_admitted": ReviewAdmittedPayload,
    "review_verdict": ReviewVerdictPayload,
    "review_fallback_admitted": ReviewFallbackAdmittedPayload,
    "land_attempted": LandAttemptedPayload,
    "restart_planned": RestartPlannedPayload,
    "restart_issued": RestartIssuedPayload,
    "probe_ran": ProbeRanPayload,
    "closed": ClosedPayload,
    "evidence_harvested": EvidenceHarvestedPayload,
    "ruling_folded": RulingFoldedPayload,
    "ruling_ignored": RulingIgnoredPayload,
    "blocked": BlockedPayload,
    "resumed": ResumedPayload,
    "flow_forked": FlowForkedPayload,
    "flow_joined": FlowJoinedPayload,
}


def parse_payload(event: str, payload: dict[str, Any]) -> Any:
    """Parse a journal payload dict into its §4.2 dataclass."""
    cls = PAYLOAD_TYPES.get(event)
    if cls is None:
        raise UnknownJournalEvent(event)
    return cls.from_dict(payload)


@dataclass
class LegRecord:
    step_id: str
    attempt: int
    flow: str
    dispatch_id: str | None = None
    execution_id: str | None = None
    model: str | None = None
    contract: str | None = None
    lane: str | None = None
    sdk_mode: str | None = None
    packet_sha256: str | None = None
    closeout_status: str | None = None
    work_outcome: str | None = None
    head_sha: str | None = None
    branch: str | None = None
    branch_point: str | None = None
    sidecar_ref: str | None = None


@dataclass
class GateResult:
    attempt: int
    verdict: GateVerdict
    head_sha: str
    ruff_exit: int | None = None
    pytest_exit: int | None = None


@dataclass
class ReviewState:
    tip_sha: str | None = None
    verdict: ReviewVerdict | None = None
    review_dispatch_id: str | None = None
    bind_status: str | None = None
    range: str | None = None
    head: str | None = None
    fallback_model: str | None = None


@dataclass
class LandState:
    tip_sha: str | None = None
    before_sha: str | None = None
    after_sha: str | None = None
    landed: bool | None = None
    refuse_reason: str | None = None


@dataclass
class RestartState:
    land_sha: str | None = None
    restart_set: tuple[str, ...] = ()
    porcelain_block: tuple[str, ...] = ()
    unmapped: tuple[str, ...] = ()
    intents: list[dict[str, str]] = field(default_factory=list)


@dataclass
class CloseState:
    todo_close: str | None = None
    friction_close: tuple[str, ...] = ()


@dataclass
class EvidenceState:
    sidecar_ref: str | None = None
    sha256: str | None = None
    status: str | None = None
    excerpt_sha256: str | None = None


@dataclass
class JournalEntry:
    seq: int
    prev_seq: int
    step_id: str
    attempt: int
    flow: str
    event: str
    payload: dict[str, Any]
    source: str = "reactor"


@dataclass
class RunState:
    """Folded mechanical-land run tip (§4.3)."""

    run_id: str | None = None
    kind: str | None = None
    source_ref: str | None = None
    policy: dict[str, Any] | None = None
    policy_sha256: str | None = None
    caller: str | None = None
    status: str | None = None
    step: str | None = None
    attempt: int = 0
    flow: str = "main"
    legs: dict[str, list[LegRecord]] = field(default_factory=dict)
    last_closeout: LegClosedPayload | None = None
    gate_results: dict[int, GateResult] = field(default_factory=dict)
    review: ReviewState = field(default_factory=ReviewState)
    land: LandState = field(default_factory=LandState)
    restart: RestartState = field(default_factory=RestartState)
    probe: dict[str, dict[str, Any]] = field(default_factory=dict)
    rulings: list[RulingFoldedPayload | RulingIgnoredPayload] = field(
        default_factory=list
    )
    pending_hold: bool = False
    blocked_reason: str | None = None
    evidence: EvidenceState | None = None
    close: CloseState = field(default_factory=CloseState)
    tip_seq: int = 0
    active_flows: set[str] = field(default_factory=lambda: {"main"})

    def result_json(self) -> dict[str, Any]:
        """§9 result shape derived from the folded state."""
        legs_out: list[dict[str, Any]] = []
        for step_id in ("implement", "fix", "review", "evidence"):
            for leg in self.legs.get(step_id, []):
                if leg.closeout_status is None:
                    continue
                legs_out.append(
                    {
                        "step": leg.step_id,
                        "attempt": leg.attempt,
                        "dispatch_id": leg.dispatch_id,
                        "closeout_status": leg.closeout_status,
                        "head_sha": leg.head_sha,
                    }
                )
        gate_out: list[dict[str, Any]] = []
        for attempt in sorted(self.gate_results):
            gr = self.gate_results[attempt]
            row: dict[str, Any] = {"attempt": attempt, "verdict": gr.verdict.value}
            if gr.ruff_exit is not None:
                row["ruff_exit"] = gr.ruff_exit
            if gr.pytest_exit is not None:
                row["pytest_exit"] = gr.pytest_exit
            gate_out.append(row)
        review_out: dict[str, Any] | None = None
        if self.review.verdict is not None:
            review_out = {
                "tip_sha": self.review.tip_sha,
                "verdict": self.review.verdict.value,
                "review_dispatch_id": self.review.review_dispatch_id,
            }
        land_out: dict[str, Any] | None = None
        if self.land.before_sha is not None:
            land_out = {
                "before_sha": self.land.before_sha,
                "after_sha": self.land.after_sha,
            }
        restart_out = {
            "restart_set": list(self.restart.restart_set),
            "intents": list(self.restart.intents),
        }
        close_out: dict[str, Any] | None = None
        if self.close.todo_close is not None:
            close_out = {
                "todo_close": self.close.todo_close,
                "friction_close": list(self.close.friction_close),
            }
        evidence_out = None
        if self.evidence and self.evidence.sidecar_ref:
            evidence_out = {
                "sidecar_ref": self.evidence.sidecar_ref,
                "sha256": self.evidence.sha256,
                "status": self.evidence.status,
            }
        journal_uri = (
            f"cortex://notes/system/runs/mechanical-land/{self.run_id}.md"
            if self.run_id
            else None
        )
        return {
            "run_id": self.run_id,
            "kind": self.kind,
            "status": self.status,
            "source_ref": self.source_ref,
            "legs": legs_out,
            "gate": gate_out,
            "review": review_out,
            "land": land_out,
            "restart": restart_out,
            "probe": dict(self.probe),
            "close": close_out,
            "evidence": evidence_out,
            "model_turns_in_control_flow": 0,
            "journal": journal_uri,
            "tip_seq": self.tip_seq,
        }


def _tuple_str(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(x) for x in value)
    return (str(value),)


def _tuple_dict(value: Any) -> tuple[dict[str, Any], ...]:
    if not value:
        return ()
    if isinstance(value, list):
        return tuple(dict(x) for x in value if isinstance(x, dict))
    if isinstance(value, dict):
        return (dict(value),)
    return ()

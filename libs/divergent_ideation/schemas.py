"""Typed record shapes for divergent-ideation-v1 (schemas P, A, L, O, B, U, X, M)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TemplateMatch(StrEnum):
    template = "template"
    variant = "variant"
    novel = "novel"


@dataclass(slots=True)
class ProblemP:
    problem_id: str
    target_statement: str
    hard_constraints: list[str]
    domain_terms_to_strip: list[str]
    run_seed: int
    benchmark_tag: str | None = None


@dataclass(slots=True)
class ActorA:
    role: str
    capability: str


@dataclass(slots=True)
class ResourceA:
    name: str
    scarce: bool
    transferable: bool


@dataclass(slots=True)
class ConstraintA:
    id: str
    statement: str
    derived_from: str


@dataclass(slots=True)
class AbstractProblemA:
    structural_goal: str
    actors: list[ActorA]
    resources: list[ResourceA]
    constraints: list[ConstraintA]
    invariants: list[str]
    decontextualizer_model: str


@dataclass(slots=True)
class LeakReportL:
    passed: bool
    hits: list[dict[str, str]]
    attempts: int


@dataclass(slots=True)
class OperatorO:
    doc_id: str
    chunk_id: str
    scope: str
    operator_statement: str
    score: float


@dataclass(slots=True)
class ConstraintCoverageBAbstract:
    constraint_id: str
    satisfied_by: str


@dataclass(slots=True)
class AbstractBriefB:
    brief_id: str
    author_model: str
    author_family: str
    operator_ids: list[str]
    mechanism_statement: str
    constraint_coverage: list[ConstraintCoverageBAbstract]


@dataclass(slots=True)
class HardConstraintCoverageB:
    constraint: str
    satisfied_by: str


@dataclass(slots=True)
class BriefB:
    brief_id: str
    author_model: str
    author_family: str
    abstract_ref: str  # engine brief_id of the source abstract brief (never model-produced)
    domain_statement: str
    hard_constraint_coverage: list[HardConstraintCoverageB]
    falsifier: str
    cost_to_test: str
    time_to_signal: str


@dataclass(slots=True)
class AuditAssignmentX:
    brief_id: str
    author_model: str
    author_family: str
    auditor_model: str
    auditor_family: str
    model_ref: str
    brief: BriefB


@dataclass(slots=True)
class AuditU:
    brief_id: str
    auditor_model: str
    auditor_family: str
    mechanism_class: str
    constraint_violations: list[str]
    re_instantiation_faithful: bool
    template_match: TemplateMatch


@dataclass(slots=True)
class RunManifestM:
    run_id: str
    pipeline: str
    problem_id: str
    run_seed: int
    pool: list[dict[str, str]]
    leak_report: dict[str, Any]
    operator_dispersion: float
    operator_subsets: list[list[str]]
    abstract_briefs: list[dict[str, Any]]
    briefs: list[dict[str, Any]]
    audit_assignments: list[dict[str, Any]]
    audits: list[dict[str, Any]]
    mechanism_clusters: list[dict[str, Any]]
    convergence_rate: float
    falsified: bool
    halted_at: str | None = None
    halt_reason: str | None = None
    benchmark_tag: str | None = None
    parse_failures: list[dict[str, Any]] = field(default_factory=list)


REQUIRED_B_FIELDS = ("brief_id", "domain_statement", "falsifier", "cost_to_test", "time_to_signal")
REQUIRED_U_FIELDS = ("brief_id", "mechanism_class", "template_match", "re_instantiation_faithful")

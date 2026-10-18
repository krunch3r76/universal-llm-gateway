"""Typed record shapes for divergent-ideation-v1 (schema P, A, L, O, B, U, X, M)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ProblemP:
    problem_id: str
    target_statement: str
    hard_constraints: list[str]
    domain_terms_to_strip: list[str]
    run_seed: int
    benchmark_tag: str | None = None


@dataclass(slots=True)
class LeakReportL:
    passed: bool
    hits: list[dict[str, str]]
    attempts: int


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

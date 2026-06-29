"""Data models for prose-fact scan."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ScanTarget:
    path: str
    reason: str
    region_start: int | None = None
    region_end: int | None = None


@dataclass(frozen=True)
class ManifestEntry:
    path: str
    reason: str


@dataclass
class CandidateClause:
    entity_id: str | None
    fact_class: str
    predicate_form: str
    clause: str
    line_start: int
    line_end: int
    bind_score: float | None = None
    advisory_only: bool = False


@dataclass
class Finding:
    verdict: str
    path: str
    entity_id: str | None
    predicate_form: str
    line_start: int
    line_end: int
    clause: str
    assertion_id: int | None = None
    severity: str = "flag"


@dataclass
class SkippedEntry:
    path: str
    reason: str


@dataclass
class FpCounters:
    citation_skip: int = 0
    precorrection_skip: int = 0
    wrong_fenced_skip: int = 0
    alignment_suppress: int = 0
    bind_suppress: int = 0
    protocol_skip: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "citation_skip": self.citation_skip,
            "precorrection_skip": self.precorrection_skip,
            "wrong_fenced_skip": self.wrong_fenced_skip,
            "alignment_suppress": self.alignment_suppress,
            "bind_suppress": self.bind_suppress,
            "protocol_skip": self.protocol_skip,
        }


@dataclass
class ScanReport:
    metadata: dict[str, Any]
    target_count: int
    excluded_count: int
    findings: list[Finding] = field(default_factory=list)
    skipped: list[SkippedEntry] = field(default_factory=list)
    friction_ids: list[int] = field(default_factory=list)
    fp_counters: FpCounters = field(default_factory=FpCounters)

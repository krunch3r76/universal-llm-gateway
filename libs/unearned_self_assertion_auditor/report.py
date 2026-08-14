"""Verdict schema and runner for the unearned-self-assertion reporter.

The structural difference this module exists to protect: a clean result must
name the denominator it used, and inability to form a denominator is a
distinct token from "checked, found nothing."
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

Verdict = Literal["finding", "checked_and_found_nothing", "could_not_check"]
DenominatorKind = Literal[
    "derived",
    "hand_list",
    "named_corpus",
    "schema_presence",
    "none",
]


@dataclass(frozen=True, slots=True)
class CoatResult:
    """One coat's verdict plus the coverage rest that makes it auditable.

    ``denominator_count`` is None when ``denominator_kind`` is ``none`` —
    that pair is the only legal encoding of "could not form a denominator."
    """

    coat_id: str
    verdict: Verdict
    denominator_kind: DenominatorKind
    denominator_source: str
    denominator_count: int | None
    coverage_rest: str
    findings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ReporterReport:
    """Full reporter payload: predicate, home, dated coats, no silent empty."""

    predicate: str
    reporter_home: str
    generated_at: str
    coats: list[CoatResult]
    could_not_check_count: int
    finding_count: int
    checked_clean_count: int

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["coats"] = [coat.to_dict() for coat in self.coats]
        return payload


def run_reporter(repo_root: Path) -> ReporterReport:
    """Run every coat against *repo_root* and refuse a silent-empty report."""
    from unearned_self_assertion_auditor.coats import all_coats

    coats = all_coats(repo_root)
    if not coats:
        raise RuntimeError("reporter produced zero coats — silent empty is the defect")
    return ReporterReport(
        predicate="the system asserts something about itself that it has not earned",
        reporter_home="script",
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        coats=coats,
        could_not_check_count=sum(1 for c in coats if c.verdict == "could_not_check"),
        finding_count=sum(1 for c in coats if c.verdict == "finding"),
        checked_clean_count=sum(
            1 for c in coats if c.verdict == "checked_and_found_nothing"
        ),
    )

"""Shadow replay falsifier harness for unified implement admission."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from implement_admission.normalize import normalize
from implement_admission.source_ref import SourceRefError


class CortexReader(Protocol):
    def entity_get(self, entity_id: str, **kwargs: Any) -> dict[str, Any]: ...


CLASSIFICATIONS = frozenset(
    {
        "match",
        "spurious_gated",
        "conflict_422",
        "mode_mismatch",
        "closeout_divergence",
    }
)


@dataclass(frozen=True, slots=True)
class ReplayCase:
    source_ref: str
    legacy_route: dict[str, Any]
    legacy_closeout_mutation: dict[str, Any] | None
    door: str


@dataclass
class ReplayReport:
    n: int
    friction_rate: float
    passed: bool
    classifications: dict[str, int] = field(default_factory=dict)
    cases: list[dict[str, Any]] = field(default_factory=list)


def classify(
    case: ReplayCase, *, cortex: CortexReader, workspaces_root: Any = None
) -> str:
    """Shadow-normalize and compare to legacy outcome."""
    try:
        spec = normalize(
            case.source_ref, cortex=cortex, workspaces_root=workspaces_root
        )
    except SourceRefError as exc:
        legacy_expects_error = case.legacy_route.get("expect_error")
        if legacy_expects_error and exc.code == legacy_expects_error:
            return "match"
        return "conflict_422"

    if spec.readiness.state.value == "gated":
        legacy_gated = case.legacy_route.get("gated", False)
        if legacy_gated:
            return "match"
        return "spurious_gated"

    routing = spec.routing
    if routing is None:
        return "spurious_gated"

    legacy_mode = case.legacy_route.get("orchestration_mode")
    legacy_style = case.legacy_route.get("executor_style")
    if legacy_mode and routing.orchestration_mode.value != legacy_mode:
        return "mode_mismatch"
    if legacy_style and routing.executor_style.value != legacy_style:
        return "mode_mismatch"

    legacy_adapter = case.legacy_closeout_mutation
    if legacy_adapter is not None:
        expected = legacy_adapter.get("adapter")
        if expected and spec.closeout.adapter.value != expected:
            return "closeout_divergence"

    return "match"


def run_replay(
    cases: Iterable[ReplayCase],
    *,
    cortex: CortexReader,
    min_n: int = 150,
    threshold: float = 0.10,
    workspaces_root: Any = None,
) -> ReplayReport:
    """Run shadow replay; passed when n >= min_n and friction_rate <= threshold."""
    case_list = list(cases)
    n = len(case_list)
    classifications: dict[str, int] = {}
    rows: list[dict[str, Any]] = []
    matches = 0

    for case in case_list:
        label = classify(case, cortex=cortex, workspaces_root=workspaces_root)
        classifications[label] = classifications.get(label, 0) + 1
        if label == "match":
            matches += 1
        rows.append(
            {
                "source_ref": case.source_ref,
                "door": case.door,
                "classification": label,
                "legacy_route": case.legacy_route,
            }
        )

    friction_rate = 0.0 if n == 0 else (n - matches) / n
    passed = (n >= min_n) and (friction_rate <= threshold)
    return ReplayReport(
        n=n,
        friction_rate=friction_rate,
        passed=passed,
        classifications=classifications,
        cases=rows,
    )

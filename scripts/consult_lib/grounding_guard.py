"""Post-run grounding validation for consult roles.

Validates that FILE: references in model responses point to real paths.
Models exceeding the hallucination threshold are auto-excluded from
future selection for the offending task.

∀ role with validate_grounding=true in requirements:
  ∀ model response: scan FILE: lines → check path existence
  hallucination_ratio > threshold ∧ invalid_count >= min_invalid ⟹ auto-exclude
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .run_artifact import validate_file_lines

if TYPE_CHECKING:
    from .core import ConsultResult

logger = logging.getLogger(__name__)

HALLUCINATION_RATIO_THRESHOLD = 0.30
MIN_INVALID_PATHS = 2
MIN_FILE_LINES = 2


@dataclass(slots=True, kw_only=True)
class ModelGroundingResult:
    """Per-model grounding validation outcome."""

    model_id: str
    valid_count: int
    invalid_count: int
    total: int
    hallucination_ratio: float
    invalid_paths: list[str]
    excluded: bool


def validate_model_grounding(
    results: list[ConsultResult],
    *,
    task: str,
    repo_root: Path | None = None,
) -> list[ModelGroundingResult]:
    """Validate FILE: line grounding per model and auto-exclude hallucinators.

    Returns per-model grounding results. Models exceeding the hallucination
    threshold are added to ~/.gateway/model-exclusions.yaml for the task.
    """
    outcomes: list[ModelGroundingResult] = []

    for result in results:
        if result.error or not result.response_text:
            continue

        validation = validate_file_lines(result.response_text, repo_root=repo_root)
        valid: list[str] = validation["valid"]
        invalid: list[str] = validation["invalid"]
        total = len(valid) + len(invalid)

        if total < MIN_FILE_LINES:
            continue

        ratio = len(invalid) / total if total > 0 else 0.0
        should_exclude = (
            len(invalid) >= MIN_INVALID_PATHS and ratio >= HALLUCINATION_RATIO_THRESHOLD
        )

        if should_exclude:
            _auto_exclude(task, result.model_id)

        outcomes.append(
            ModelGroundingResult(
                model_id=result.model_id,
                valid_count=len(valid),
                invalid_count=len(invalid),
                total=total,
                hallucination_ratio=round(ratio, 3),
                invalid_paths=invalid,
                excluded=should_exclude,
            )
        )

    return outcomes


_EXCLUSIONS_PATH = Path.home() / ".gateway" / "model-exclusions.yaml"


def _auto_exclude(task: str, model_id: str) -> None:
    """Add model to exclusion list for a task, with stderr notification."""
    import yaml

    exclusions: dict[str, list[str]] = {}
    if _EXCLUSIONS_PATH.exists():
        exclusions = yaml.safe_load(_EXCLUSIONS_PATH.read_text()) or {}

    task_list = exclusions.setdefault(task, [])
    if model_id in task_list:
        return

    task_list.append(model_id)
    _EXCLUSIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _EXCLUSIONS_PATH.write_text(
        yaml.dump(exclusions, default_flow_style=False, sort_keys=True)
    )
    print(
        f"GROUNDING GUARD: auto-excluded '{model_id}' from task '{task}' "
        f"(hallucinated file paths)",
        file=sys.stderr,
    )
    logger.warning(
        "Auto-excluded model=%s from task=%s due to path hallucination",
        model_id,
        task,
    )


def report_grounding_results(
    outcomes: list[ModelGroundingResult],
) -> None:
    """Print grounding validation summary to stderr."""
    for outcome in outcomes:
        if outcome.invalid_count == 0:
            continue
        status = "AUTO-EXCLUDED" if outcome.excluded else "warning"
        print(
            f"GROUNDING [{status}] {outcome.model_id}: "
            f"{outcome.invalid_count}/{outcome.total} FILE paths invalid "
            f"(ratio={outcome.hallucination_ratio:.0%})",
            file=sys.stderr,
        )
        for path in outcome.invalid_paths[:5]:
            print(f"  invalid: {path}", file=sys.stderr)


def write_grounding_artifact(
    outcomes: list[ModelGroundingResult],
    *,
    run_dir: Path,
) -> None:
    """Write per-model grounding results to the run artifact directory."""
    if not outcomes:
        return
    data: list[dict[str, str | int | float | list[str] | bool]] = [
        {
            "model_id": o.model_id,
            "valid_count": o.valid_count,
            "invalid_count": o.invalid_count,
            "total": o.total,
            "hallucination_ratio": o.hallucination_ratio,
            "invalid_paths": o.invalid_paths,
            "excluded": o.excluded,
        }
        for o in outcomes
    ]
    path = run_dir / "grounding_validation.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
